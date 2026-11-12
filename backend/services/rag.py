"""RAG service — TF-IDF retrieval from Branch Rickey CSV (no PyTorch required).

Three things sit on top of plain TF-IDF, each measured in eval/retrieval_eval.py:

  1. Vocabulary normalization. Rickey wrote "fast ball", "change of pace" and
     "control"; a college scout today writes "fastball", "changeup" and
     "command". Without a shared spelling those never match.
  2. Role filter. A pitcher's notes should not come back with a hitter's report.
     When the notes clearly describe a pitcher (or a position player), reports
     that clearly describe the other role are dropped.
  3. Duplicate collapse. The corpus holds pages that were transcribed twice;
     without this the same report can fill two of the five slots.

Every hit carries a confidence band so the caller can tell a real match from
the best of a bad lot. Weak hits are returned (the UI shows them, labeled) but
are never placed in the model's context as historical evidence.
"""
import re
from functools import lru_cache
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

BASE_DIR = Path(__file__).resolve().parents[2]
DATA_DIR  = BASE_DIR / "data"
CSV_PATH  = DATA_DIR / "branch-rickey-scouting.csv"
TOP_K     = 5

ROLE_PITCHER = "pitcher"
ROLE_HITTER  = "hitter"      # any position player
ROLE_MIXED   = "mixed"
ROLE_UNKNOWN = "unknown"

# Confidence bands on the TF-IDF cosine score. Calibrated on the gold set in
# eval/gold/retrieval_gold.json: see "Score calibration" in
# eval/results/retrieval_eval.md for the hit rates behind these two numbers.
STRONG_SCORE = 0.25
WEAK_SCORE   = 0.15
DUPLICATE_COSINE = 0.95


# ── Vocabulary normalization ─────────────────────────────────────────────────
# Case-sensitive position abbreviations, expanded before lowercasing so "OF"
# the position is not confused with "of" the word.
_ABBREVIATIONS = [
    (re.compile(r"\bRHP\b"), "right hand pitcher"),
    (re.compile(r"\bLHP\b"), "left hand pitcher"),
    (re.compile(r"\bSS\b"), "shortstop"),
    (re.compile(r"\bCF\b"), "center field outfielder"),
    (re.compile(r"\b(?:OF|LF|RF)\b"), "outfielder"),
    (re.compile(r"\b1B\b"), "first baseman"),
    (re.compile(r"\b2B\b"), "second baseman"),
    (re.compile(r"\b3B\b"), "third baseman"),
    (re.compile(r"\b(?:IF|INF)\b"), "infielder"),
    (re.compile(r"\bUTIL\b"), "utility"),
    (re.compile(r"(?m)^C(?=[,/ ])"), "catcher"),
]

# Spelling and era differences, applied to the lowercased text of both the
# corpus and the query. General baseball vocabulary only.
_SPELLINGS = [
    (re.compile(r"\bfast[\s-]?balls?\b|\bheaters?\b"), "fastball"),
    (re.compile(r"\bcurve[\s-]?balls?\b|\bcurves?\b"), "curveball"),
    (re.compile(r"\bbreaking (?:balls?|stuff|pitch(?:es)?)\b"), "curveball"),
    (re.compile(r"\bchange[\s-]?ups?\b|\bchanges? of (?:pace|speed)\b"), "changeup"),
    (re.compile(r"\bknuckle[\s-]?balls?\b"), "knuckleball"),
    (re.compile(r"\bscrew[\s-]?balls?\b"), "screwball"),
    (re.compile(r"\b(right|left)[\s-]?hand(?:ed|ers?)?\b"), r"\1 hand"),
    (re.compile(r"\bvelo(?:city)?\b"), "velocity speed"),
    (re.compile(r"\bcommand(?:s|ed)?\b"), "control"),
    (re.compile(r"\bstrike[\s-]?throw(?:er|ing)\b"), "control"),
    (re.compile(r"\barm slots?\b|\bslots?\b"), "delivery angle"),
    (re.compile(r"\bbaserunn(er|ers|ing)\b"), r"base runn\1"),
    (re.compile(r"\bswing[\s-]and[\s-]miss\b|\bwhiffs?\b"), "strike out"),
    (re.compile(r"\bpop\b(?!-)"), "power"),
]


def _expand_position_line(text: str) -> str:
    """Expand position abbreviations on a note's first two lines only.

    Rickey's pages open with ALL-CAPS headers ("GAMES OF MAY 17TH", "IF HE
    CAN HIT"), where a document-wide pass turns "OF" into "outfielder" and "IF"
    into "infielder". Modern notes put the abbreviations on the position line,
    so that is the only place they are expanded.
    """
    lines = text.split("\n")
    head = "\n".join(lines[:2])
    for pattern, replacement in _ABBREVIATIONS:
        head = pattern.sub(replacement, head)
    return "\n".join([head] + lines[2:])


def normalize(text: str, *, expand_positions: bool = True) -> str:
    """Put a modern note and a 1950s report into one vocabulary.

    expand_positions is off for corpus documents: Rickey did not write "RHP"
    or "SS", and his capitalized headers would be misread as abbreviations.
    """
    text = str(text)
    if expand_positions:
        text = _expand_position_line(text)
    text = text.lower()
    for pattern, replacement in _SPELLINGS:
        text = pattern.sub(replacement, text)
    return text


# ── Role inference ───────────────────────────────────────────────────────────
_PITCHER_TERMS = re.compile(
    r"\b(?:fastball|curveball|changeup|knuckleball|screwball|slider|sinker|"
    r"pitcher|pitchers|pitches|pitched|pitching|delivery|control|stuff|balk)\b"
)
_HITTER_TERMS = re.compile(
    r"\b(?:hitter|hitters|hitting|batsman|batting|bat|power|swing|swings|"
    r"swinging|stance|stroke|runner|running|outfielder|infielder|shortstop|"
    r"catcher|baseman|outfield|infield|fielder)\b"
)
# What a scout writes on the position line of a note.
_HEADER_PITCHER = re.compile(r"\b(?:pitcher|reliever|closer|starter|southpaw)\b")
_HEADER_HITTER = re.compile(
    r"\b(?:shortstop|outfielder|infielder|catcher|baseman|utility|hitter|dh)\b"
)


def _term_role(normalized: str) -> str:
    """Role from how often pitching and position-player words appear."""
    p = len(_PITCHER_TERMS.findall(normalized))
    h = len(_HITTER_TERMS.findall(normalized))
    if p >= 2 and p >= 2 * h:
        return ROLE_PITCHER
    if h >= 2 and h >= 2 * p:
        return ROLE_HITTER
    if p >= 2 and h >= 2:
        return ROLE_MIXED
    return ROLE_UNKNOWN


def infer_role(text: str) -> str:
    """Role of the player a set of notes describes.

    The position line ("RHP, junior, 6'3") is the strongest signal, so the
    first two lines are read first. A two-way or converted player names both
    roles there; that falls through to the wording of the whole note.
    """
    normalized = normalize(text)
    header = " ".join(normalized.strip().splitlines()[:2])[:200]
    is_pitcher = bool(_HEADER_PITCHER.search(header))
    is_hitter = bool(_HEADER_HITTER.search(header))
    if is_pitcher and not is_hitter:
        return ROLE_PITCHER
    if is_hitter and not is_pitcher:
        return ROLE_HITTER
    role = _term_role(normalized)
    return role if role in (ROLE_PITCHER, ROLE_HITTER) else ROLE_UNKNOWN


def _roles_conflict(query_role: str, doc_role: str) -> bool:
    return {query_role, doc_role} == {ROLE_PITCHER, ROLE_HITTER}


def confidence_band(score: float) -> str:
    if score >= STRONG_SCORE:
        return "strong"
    if score >= WEAK_SCORE:
        return "moderate"
    return "weak"


# ── Index ────────────────────────────────────────────────────────────────────
_YEAR_RE = re.compile(r"Scouting reports; (\d{4}|Undated)")


@lru_cache(maxsize=2)
def _load_index(normalized: bool = True):
    """Load CSV, build TF-IDF matrix (cached after first call).

    normalized=False rebuilds the index exactly as it was before the
    vocabulary fix. Only the evaluation uses it, as the "before" system.
    """
    df = pd.read_csv(CSV_PATH)

    # Keep rows with actual transcription text
    df = df[df["Transcription"].notna() & (df["Transcription"].str.strip() != "")]
    df = df.reset_index(drop=True)

    texts = df["Transcription"].tolist()
    corpus_norm = [normalize(t, expand_positions=False) for t in texts]
    indexed = corpus_norm if normalized else texts

    vectorizer = TfidfVectorizer(
        max_features=20_000,
        ngram_range=(1, 2),
        sublinear_tf=True,
    )
    matrix = vectorizer.fit_transform(indexed)

    docs = []
    for i, row in df.iterrows():
        item = row["Item"] if isinstance(row["Item"], str) else "Rickey Doc"
        year = _YEAR_RE.search(item)
        docs.append({
            "asset": str(row["Asset"]),
            "source": item,
            "year": year.group(1) if year else None,
            "scan_url": row["DownloadUrl"] if isinstance(row["DownloadUrl"], str) else None,
            "text": texts[i],
            "role": _term_role(corpus_norm[i]),
        })

    return vectorizer, matrix, docs, _duplicate_groups(matrix)


def _duplicate_groups(matrix) -> list[int]:
    """Map every document to the first document it nearly duplicates."""
    sims = (matrix @ matrix.T).toarray()
    canonical = list(range(matrix.shape[0]))
    for i in range(matrix.shape[0]):
        if canonical[i] != i:
            continue
        for j in np.nonzero(sims[i, i + 1:] >= DUPLICATE_COSINE)[0]:
            j = int(j) + i + 1
            if canonical[j] == j:
                canonical[j] = i
    return canonical


def canonical_asset(asset: str) -> str:
    """The asset id a duplicate transcription collapses onto."""
    _, _, docs, canonical = _load_index(True)
    for i, doc in enumerate(docs):
        if doc["asset"] == asset:
            return docs[canonical[i]]["asset"]
    return asset


def retrieve(query: str, k: int = TOP_K, *, normalized: bool = True,
             role_filter: bool = True, dedupe: bool = True) -> list[dict]:
    """Return top-k relevant Rickey documents for a query.

    The three keyword flags exist so the evaluation can switch each fix off
    and measure it. Production callers leave them at their defaults.
    """
    vectorizer, matrix, docs, canonical = _load_index(normalized)

    q_vec = vectorizer.transform([normalize(query) if normalized else query])
    scores = cosine_similarity(q_vec, matrix).flatten()
    query_role = infer_role(query) if role_filter else ROLE_UNKNOWN

    results, seen = [], set()
    for idx in np.argsort(scores)[::-1]:
        if len(results) >= k or scores[idx] == 0:
            break
        doc = docs[idx]
        if role_filter and _roles_conflict(query_role, doc["role"]):
            continue
        if dedupe:
            if canonical[idx] in seen:
                continue
            seen.add(canonical[idx])
        score = float(scores[idx])
        results.append({
            "asset": doc["asset"],
            "source": doc["source"],
            "year": doc["year"],
            "scan_url": doc["scan_url"],
            "text": doc["text"][:600],   # cap chunk length
            "score": score,
            "confidence": confidence_band(score),
            "role": doc["role"],
        })
    return results


def usable(hits: list[dict]) -> list[dict]:
    """Hits strong enough to hand the model as historical evidence."""
    return [h for h in hits if h["confidence"] != "weak"]


def format_context(hits: list[dict]) -> str:
    """Prompt block for the usable hits; empty string when none qualify."""
    hits = usable(hits)
    if not hits:
        return ""
    lines = ["--- Branch Rickey Reference ---"]
    for h in hits:
        lines.append(f"[{h['source']}] {h['text']}")
    lines.append("--- End Reference ---")
    return "\n".join(lines)


def match_summary(hits: list[dict], query: str) -> dict:
    """What the UI needs to show how good the historical match was.

    quality is the band of the best hit: "strong", "moderate", "weak", or
    "none" when nothing matched at all.
    """
    best = hits[0]["confidence"] if hits else "none"
    return {
        "quality": best,
        "used_as_evidence": len(usable(hits)),
        "note_role": infer_role(query),
        "matches": hits,
        "bands": {"strong_from": STRONG_SCORE, "moderate_from": WEAK_SCORE},
    }


def context_block(query: str, k: int = TOP_K) -> str:
    """Return a formatted context string for injection into Claude prompts."""
    return format_context(retrieve(query, k))
