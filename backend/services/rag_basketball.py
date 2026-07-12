"""Basketball RAG — TF-IDF retrieval over Stathead / Basketball-Reference CSV exports.

Drop any number of CSV exports into data/basketball/ and restart the server.
Each row is converted to a natural-language blurb so free-text scouting notes
can match against it. Missing folder or no files = empty context (graceful).
"""
from functools import lru_cache
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

BASE_DIR = Path(__file__).resolve().parents[2]
DATA_DIR = BASE_DIR / "data" / "basketball"
TOP_K = 5

# Column → friendly label, in display order. Covers common
# Basketball-Reference / Stathead export headers (per-game and totals).
STAT_LABELS = [
    ("PTS", "PTS"), ("TRB", "REB"), ("REB", "REB"), ("AST", "AST"),
    ("STL", "STL"), ("BLK", "BLK"), ("TOV", "TOV"), ("MP", "MIN"),
    ("FG%", "FG%"), ("3P%", "3P%"), ("FT%", "FT%"), ("eFG%", "eFG%"),
    ("TS%", "TS%"), ("PER", "PER"), ("WS", "WS"), ("BPM", "BPM"),
    ("VORP", "VORP"), ("USG%", "USG%"), ("G", "G"), ("GS", "GS"),
]
PLAYER_COLS = ["Player", "Name", "player", "name"]
META_COLS = [("Pos", "Pos"), ("Tm", "Team"), ("Team", "Team"),
             ("Season", "Season"), ("Year", "Season"), ("Age", "Age")]


def _row_to_blurb(row: pd.Series, columns: list[str]) -> tuple[str, str] | None:
    """Convert one stat row into (display_text, searchable_text).

    The searchable text excludes the player's name so scouting notes that
    happen to contain common name words ("at will", a prospect named Carter)
    don't false-match database players by name instead of by profile.
    """
    player = ""
    for c in PLAYER_COLS:
        if c in columns and pd.notna(row.get(c)):
            player = str(row[c]).strip()
            break
    if not player:
        return None

    meta, seen = [], set()
    for col, label in META_COLS:
        if label in seen:
            continue
        if col in columns and pd.notna(row.get(col)) and str(row[col]).strip():
            meta.append(f"{label} {row[col]}")
            seen.add(label)

    stats, seen = [], set()
    for col, label in STAT_LABELS:
        if label in seen:
            continue
        if col in columns and pd.notna(row.get(col)):
            val = row[col]
            if isinstance(val, float):
                val = round(val, 3 if "%" in col else 1)
            stats.append(f"{val} {label}")
            seen.add(label)

    tags = _descriptor_tags(row, columns)
    body_parts = []
    if meta:
        body_parts.append("(" + ", ".join(meta) + ")")
    if stats:
        body_parts.append("— " + ", ".join(stats))
    if tags:
        body_parts.append("· " + ", ".join(tags))
    body = " ".join(body_parts)
    return f"{player} {body}", body


def _num(row: pd.Series, columns: list[str], *names) -> float | None:
    for n in names:
        if n in columns and pd.notna(row.get(n)):
            try:
                return float(row[n])
            except (TypeError, ValueError):
                continue
    return None


POSITION_WORDS = {
    "PG": "point guard, guard, ball handler",
    "SG": "shooting guard, guard, wing",
    "SF": "small forward, forward, wing",
    "PF": "power forward, forward, big",
    "C":  "center, big man, post player",
}


def _descriptor_tags(row: pd.Series, columns: list[str]) -> list[str]:
    """Derive scouting-language descriptors so free-text notes can TF-IDF match stat rows."""
    tags = []
    pos = row.get("Pos")
    if "Pos" in columns and pd.notna(pos):
        for code in str(pos).replace("/", "-").split("-"):
            if code.strip().upper() in POSITION_WORDS:
                tags.append(POSITION_WORDS[code.strip().upper()])
    pts = _num(row, columns, "PTS")
    ast = _num(row, columns, "AST")
    reb = _num(row, columns, "TRB", "REB")
    blk = _num(row, columns, "BLK")
    stl = _num(row, columns, "STL")
    tp_pct = _num(row, columns, "3P%")
    tpa = _num(row, columns, "3PA")
    ts = _num(row, columns, "TS%")
    fg_pct = _num(row, columns, "FG%")

    if pts is not None:
        if pts >= 20:
            tags.append("high volume scorer, primary scoring option")
        elif pts >= 12:
            tags.append("reliable scorer")
    if ast is not None and ast >= 5:
        tags.append("playmaker, strong passer and assist creator")
    if reb is not None and reb >= 8:
        tags.append("strong rebounder, controls the glass")
    if blk is not None and blk >= 1.5:
        tags.append("rim protector, shot blocker")
    if stl is not None and stl >= 1.5:
        tags.append("disruptive perimeter defender, generates steals")
    if tp_pct is not None and tp_pct >= 0.36 and (tpa is None or tpa >= 2):
        tags.append("good three point shooter, spacing threat")
    if (ts is not None and ts >= 0.58) or (fg_pct is not None and fg_pct >= 0.52):
        tags.append("efficient scorer, high percentage shooting")
    return tags


@lru_cache(maxsize=1)
def _load_index():
    """Scan data/basketball/*.csv, build TF-IDF index. Returns None if no data."""
    if not DATA_DIR.exists():
        return None

    texts, search_texts, sources = [], [], []
    for csv_path in sorted(DATA_DIR.glob("*.csv")):
        try:
            df = pd.read_csv(csv_path)
        except Exception:
            continue
        cols = list(df.columns)
        for _, row in df.iterrows():
            blurb = _row_to_blurb(row, cols)
            if blurb:
                texts.append(blurb[0])
                search_texts.append(blurb[1])
                sources.append(csv_path.stem)

    if not texts:
        return None

    vectorizer = TfidfVectorizer(
        max_features=20_000, ngram_range=(1, 2), sublinear_tf=True,
        stop_words="english",
    )
    matrix = vectorizer.fit_transform(search_texts)
    return vectorizer, matrix, texts, sources


def retrieve(query: str, k: int = TOP_K) -> list[dict]:
    """Return top-k statistically similar player blurbs for a query."""
    index = _load_index()
    if index is None:
        return []
    vectorizer, matrix, texts, sources = index

    q_vec = vectorizer.transform([query])
    scores = cosine_similarity(q_vec, matrix).flatten()
    top_indices = np.argsort(scores)[::-1][:k]

    results = []
    for idx in top_indices:
        if scores[idx] == 0:
            continue
        results.append({
            "source": sources[idx],
            "text": texts[idx][:600],
            "score": float(scores[idx]),
        })
    return results


def context_block(query: str, k: int = TOP_K) -> str:
    """Formatted context string for injection into Claude prompts ('' if no data)."""
    hits = retrieve(query, k)
    if not hits:
        return ""
    lines = ["--- Statistical Player Comps (Basketball Reference) ---"]
    for h in hits:
        lines.append(f"[{h['source']}] {h['text']}")
    lines.append("--- End Comps ---")
    return "\n".join(lines)
