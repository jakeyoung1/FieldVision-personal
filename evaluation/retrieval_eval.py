"""Retrieval evaluation for the Branch Rickey matcher.

Scores the production retriever against a labeled gold set and against two
baselines, so "the matches are relevant" is a measured claim.

    .venv/bin/python -m evaluation.retrieval_eval            # print + write results
    .venv/bin/python -m evaluation.retrieval_eval --pool     # list hits with no role label

Systems compared (all return the top 10 Rickey documents for a note):
  random          uniform random documents, 2,000 seeded draws per note
  bm25            Okapi BM25 over the raw page text (k1=1.5, b=0.75)
  tfidf_before    the retriever as it shipped before this evaluation existed
  + role filter   tfidf_before plus the pitcher / position-player filter
  + vocabulary    tfidf_before plus modern-to-1950s vocabulary normalization
  production      all three fixes on: vocabulary, role filter, duplicate collapse

Metrics, per note then averaged:
  precision@5     labeled reports in the top 5, divided by 5
  recall@5        labeled reports in the top 5, divided by labeled reports
  MRR@10          1 / rank of the first labeled report (0 if none in the top 10)
  role mismatch   share of top-5 hits that describe the other role, judged
                  against the hand role labels in gold/doc_roles.json

A page transcribed twice counts once: duplicates are folded onto one id before
scoring, so returning both copies wastes a slot instead of earning two hits.

Read evaluation/gold/retrieval_gold.json "_meta" before quoting any number.
The labels are AI-drafted and not yet human-audited.
"""
import argparse
import json
import random
from pathlib import Path

import numpy as np
from sklearn.feature_extraction.text import CountVectorizer

from backend.services import rag

HERE = Path(__file__).parent
GOLD_PATH = HERE / "gold" / "retrieval_gold.json"
ROLES_PATH = HERE / "gold" / "doc_roles.json"
RESULTS_DIR = HERE / "results"
DEMO_NOTES = HERE.parent / "static" / "demo" / "scouting_demo.txt"

K = 5
DEPTH = 10
QUERY_CHARS = 500          # /api/analyze sends the first 500 characters
RANDOM_DRAWS = 2000
RANDOM_SEED = 20261112
SCORE_BINS = [0.0, 0.10, 0.15, 0.20, 0.25, 0.30, 1.01]


# ── Metrics ──────────────────────────────────────────────────────────────────
def precision_at_k(ranked: list[str], relevant: set[str], k: int = K) -> float:
    return len(set(ranked[:k]) & relevant) / k


def recall_at_k(ranked: list[str], relevant: set[str], k: int = K) -> float:
    return len(set(ranked[:k]) & relevant) / len(relevant) if relevant else 0.0


def reciprocal_rank(ranked: list[str], relevant: set[str]) -> float:
    for rank, doc in enumerate(ranked, 1):
        if doc in relevant:
            return 1.0 / rank
    return 0.0


def mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def roles_mismatch(query_role: str, doc_label: str) -> bool:
    """True when a hand-labeled document describes the other role."""
    return {query_role, doc_label} == {rag.ROLE_PITCHER, rag.ROLE_HITTER}


# ── Data ─────────────────────────────────────────────────────────────────────
def load_gold() -> dict:
    return json.loads(GOLD_PATH.read_text())


def load_role_labels() -> dict[str, str]:
    if not ROLES_PATH.exists():
        return {}
    return json.loads(ROLES_PATH.read_text())["labels"]


def corpus() -> list[dict]:
    return rag._load_index(True)[2]


def canonical_map() -> dict[str, str]:
    """asset id -> the asset id its duplicate group collapses onto."""
    _, _, docs, canonical = rag._load_index(True)
    return {d["asset"]: docs[canonical[i]]["asset"] for i, d in enumerate(docs)}


# ── Systems ──────────────────────────────────────────────────────────────────
def _tfidf(**flags):
    def run(query: str) -> list[dict]:
        return rag.retrieve(query, DEPTH, **flags)
    return run


class BM25:
    """Okapi BM25 over the raw page text. The standard lexical baseline."""

    def __init__(self, texts: list[str], k1: float = 1.5, b: float = 0.75):
        self.k1, self.b = k1, b
        self.vectorizer = CountVectorizer(lowercase=True)
        self.tf = self.vectorizer.fit_transform(texts).tocsc()
        n_docs = self.tf.shape[0]
        self.doc_len = np.asarray(self.tf.sum(axis=1)).ravel()
        self.avg_len = self.doc_len.mean()
        df = np.asarray((self.tf > 0).sum(axis=0)).ravel()
        self.idf = np.log((n_docs - df + 0.5) / (df + 0.5) + 1.0)

    def scores(self, query: str) -> np.ndarray:
        q = self.vectorizer.transform([query])
        out = np.zeros(self.tf.shape[0])
        norm = self.k1 * (1 - self.b + self.b * self.doc_len / self.avg_len)
        for term in q.indices:
            tf = self.tf[:, term].toarray().ravel()
            out += self.idf[term] * tf * (self.k1 + 1) / (tf + norm)
        return out


def _bm25_system():
    docs = corpus()
    bm25 = BM25([d["text"] for d in docs])

    def run(query: str) -> list[dict]:
        scores = bm25.scores(query)
        top = np.argsort(-scores, kind="stable")[:DEPTH]
        return [{"asset": docs[i]["asset"], "score": float(scores[i])}
                for i in top if scores[i] > 0]
    return run


def systems() -> dict:
    return {
        "bm25": _bm25_system(),
        "tfidf_before": _tfidf(normalized=False, role_filter=False, dedupe=False),
        "tfidf_before + role filter": _tfidf(normalized=False, role_filter=True, dedupe=False),
        "tfidf_before + vocabulary": _tfidf(normalized=True, role_filter=False, dedupe=False),
        "production": _tfidf(),
    }


# ── Scoring ──────────────────────────────────────────────────────────────────
def score_system(run, queries: list[dict], canon: dict[str, str],
                 role_labels: dict[str, str]) -> dict:
    p, r, rr = [], [], []
    mismatched = labeled = unlabeled = 0
    per_query = []
    for q in queries:
        hits = run(q["note"][:QUERY_CHARS])
        ranked = [canon.get(h["asset"], h["asset"]) for h in hits]
        relevant = {canon.get(a, a) for a in q["relevant"]}
        p.append(precision_at_k(ranked, relevant))
        r.append(recall_at_k(ranked, relevant))
        rr.append(reciprocal_rank(ranked, relevant))
        for h in hits[:K]:
            label = role_labels.get(h["asset"])
            if label is None:
                unlabeled += 1
                continue
            labeled += 1
            mismatched += roles_mismatch(q["role"], label)
        per_query.append({
            "id": q["id"],
            "top5": [h["asset"] for h in hits[:K]],
            "scores": [round(h["score"], 4) for h in hits[:K]],
            "precision_at_5": p[-1], "recall_at_5": r[-1], "rr": rr[-1],
        })
    return {
        "precision_at_5": round(mean(p), 4),
        "recall_at_5": round(mean(r), 4),
        "mrr_at_10": round(mean(rr), 4),
        "role_mismatch_at_5": round(mismatched / labeled, 4) if labeled else None,
        "role_mismatched_hits": mismatched,
        "role_labeled_hits": labeled,
        "role_unlabeled_hits": unlabeled,
        "per_query": per_query,
    }


def score_random(queries: list[dict], canon: dict[str, str],
                 role_labels: dict[str, str]) -> dict:
    """Uniform random retrieval, averaged over seeded draws.

    Role mismatch for random is estimated only from documents that carry a hand
    label, so it is the mismatch rate inside the labeled pool, not the corpus.
    """
    rng = random.Random(RANDOM_SEED)
    assets = [d["asset"] for d in corpus()]
    p, r, rr = [], [], []
    for q in queries:
        relevant = {canon.get(a, a) for a in q["relevant"]}
        qp, qr, qrr = [], [], []
        for _ in range(RANDOM_DRAWS):
            ranked = [canon[a] for a in rng.sample(assets, DEPTH)]
            qp.append(precision_at_k(ranked, relevant))
            qr.append(recall_at_k(ranked, relevant))
            qrr.append(reciprocal_rank(ranked, relevant))
        p.append(mean(qp))
        r.append(mean(qr))
        rr.append(mean(qrr))
    labeled = [(q["role"], label) for q in queries for label in role_labels.values()]
    mismatch = mean([float(roles_mismatch(a, b)) for a, b in labeled]) if labeled else None
    return {
        "precision_at_5": round(mean(p), 4),
        "recall_at_5": round(mean(r), 4),
        "mrr_at_10": round(mean(rr), 4),
        "role_mismatch_at_5": round(mismatch, 4) if mismatch is not None else None,
        "draws_per_query": RANDOM_DRAWS,
    }


def score_calibration(production: dict, queries: list[dict], canon: dict[str, str],
                      role_labels: dict[str, str]) -> list[dict]:
    """How often a production hit is right, by score bin. Sets the bands."""
    by_id = {q["id"]: q for q in queries}
    rows = []
    for lo, hi in zip(SCORE_BINS, SCORE_BINS[1:]):
        hits = gold = role_ok = role_seen = 0
        for pq in production["per_query"]:
            q = by_id[pq["id"]]
            relevant = {canon.get(a, a) for a in q["relevant"]}
            for asset, score in zip(pq["top5"], pq["scores"]):
                if not lo <= score < hi:
                    continue
                hits += 1
                gold += canon.get(asset, asset) in relevant
                label = role_labels.get(asset)
                if label is not None:
                    role_seen += 1
                    role_ok += not roles_mismatch(q["role"], label)
        rows.append({
            "score_from": lo, "score_to": min(hi, 1.0), "hits": hits,
            "labeled_report_rate": round(gold / hits, 4) if hits else None,
            "role_compatible_rate": round(role_ok / role_seen, 4) if role_seen else None,
        })
    return rows


def score_classifiers(queries: list[dict], role_labels: dict[str, str]) -> dict:
    """Accuracy of the two rule-based role guesses against hand labels."""
    query_ok = sum(rag.infer_role(q["note"]) == q["role"] for q in queries)
    by_asset = {d["asset"]: d["role"] for d in corpus()}
    decided = wrong = 0
    for asset, label in role_labels.items():
        guess = by_asset.get(asset)
        if guess in (rag.ROLE_PITCHER, rag.ROLE_HITTER) and label in (rag.ROLE_PITCHER, rag.ROLE_HITTER):
            decided += 1
            wrong += guess != label
    return {
        "note_role_correct": query_ok,
        "note_role_total": len(queries),
        "doc_role_confident_and_labeled": decided,
        "doc_role_confident_but_wrong": wrong,
    }


def demo_cases(role_labels: dict[str, str]) -> list[dict]:
    """The two demo notes the outside reviewer ran. Before and after."""
    cases = []
    for block in DEMO_NOTES.read_text().split("---"):
        note = block.strip()
        if not note:
            continue
        role = rag.infer_role(note)
        case = {"player": note.splitlines()[0], "note_role": role}
        for name, flags in (("before", dict(normalized=False, role_filter=False, dedupe=False)),
                            ("after", {})):
            hits = rag.retrieve(note[:QUERY_CHARS], K, **flags)
            case[name] = [{
                "asset": h["asset"], "score": round(h["score"], 3),
                "confidence": h["confidence"],
                "hand_role": role_labels.get(h["asset"], "unlabeled"),
                "mismatch": roles_mismatch(role, role_labels.get(h["asset"], "")),
            } for h in hits]
        cases.append(case)
    return cases


def run_all() -> dict:
    gold = load_gold()
    queries = gold["queries"]
    canon = canonical_map()
    role_labels = load_role_labels()
    results = {name: score_system(run, queries, canon, role_labels)
               for name, run in systems().items()}
    return {
        "gold": {
            "queries": len(queries),
            "pitcher_notes": sum(q["role"] == rag.ROLE_PITCHER for q in queries),
            "position_player_notes": sum(q["role"] == rag.ROLE_HITTER for q in queries),
            "labeled_reports": sum(len(q["relevant"]) for q in queries),
            "label_status": gold["_meta"]["label_status"],
            "notes_are_synthetic": gold["_meta"]["notes_are_synthetic"],
            "role_labels": len(role_labels),
        },
        "corpus_documents": len(corpus()),
        "systems": {"random": score_random(queries, canon, role_labels), **results},
        "calibration": score_calibration(results["production"], queries, canon, role_labels),
        "bands": {"strong_from": rag.STRONG_SCORE, "moderate_from": rag.WEAK_SCORE},
        "classifiers": score_classifiers(queries, role_labels),
        "demo_cases": demo_cases(role_labels),
    }


# ── Output ───────────────────────────────────────────────────────────────────
def _pct(value) -> str:
    return "n/a" if value is None else f"{100 * value:.1f}%"


def to_markdown(res: dict) -> str:
    g = res["gold"]
    lines = [
        "# Retrieval evaluation",
        "",
        "Generated by `python -m evaluation.retrieval_eval`. Do not edit by hand.",
        "",
        f"**Label status: {g['label_status']}.** The {g['queries']} notes are synthetic "
        "(fictional players, written by AI from a seed Rickey report) and the labels "
        "have not been audited by a person. Numbers are provisional.",
        "",
        f"- Gold set: {g['queries']} notes ({g['pitcher_notes']} pitchers, "
        f"{g['position_player_notes']} position players), {g['labeled_reports']} labeled reports",
        f"- Corpus: {res['corpus_documents']:,} Rickey documents",
        f"- Hand role labels: {g['role_labels']} documents",
        "",
        "## Systems",
        "",
        "| System | precision@5 | recall@5 | MRR@10 | role mismatch@5 |",
        "|---|---|---|---|---|",
    ]
    for name, s in res["systems"].items():
        lines.append(f"| {name} | {s['precision_at_5']:.3f} | {s['recall_at_5']:.3f} | "
                     f"{s['mrr_at_10']:.3f} | {_pct(s['role_mismatch_at_5'])} |")
    prod, before = res["systems"]["production"], res["systems"]["tfidf_before"]
    lines += [
        "",
        f"Role mismatch counts: before {before['role_mismatched_hits']} of "
        f"{before['role_labeled_hits']} labeled top-5 hits; production "
        f"{prod['role_mismatched_hits']} of {prod['role_labeled_hits']}. "
        f"Hits with no hand label: before {before['role_unlabeled_hits']}, "
        f"production {prod['role_unlabeled_hits']}.",
        "",
        "precision@5 is a lower bound: only reports on the seed player are labeled, "
        "so a good match on a different player counts as a miss.",
        "",
        "## Score calibration (production, top-5 hits on the gold set)",
        "",
        "| TF-IDF cosine | hits | is a labeled report | role compatible |",
        "|---|---|---|---|",
    ]
    for row in res["calibration"]:
        lines.append(f"| {row['score_from']:.2f} to {row['score_to']:.2f} | {row['hits']} | "
                     f"{_pct(row['labeled_report_rate'])} | {_pct(row['role_compatible_rate'])} |")
    b, c = res["bands"], res["classifiers"]
    lines += [
        "",
        f"Bands used by the app: strong at {b['strong_from']:.2f} and above, moderate from "
        f"{b['moderate_from']:.2f}, weak below. Weak hits are shown to the user with a label "
        "and are not given to the model as evidence.",
        "",
        "## Role rules checked against hand labels",
        "",
        f"- Note role inferred correctly: {c['note_role_correct']} of {c['note_role_total']}",
        f"- Document role, where the rule was confident and a hand label exists: "
        f"{c['doc_role_confident_but_wrong']} wrong of {c['doc_role_confident_and_labeled']}",
        "",
        "## The reviewer's demo notes",
        "",
    ]
    for case in res["demo_cases"]:
        lines.append(f"### {case['player']} (read as: {case['note_role']})")
        lines.append("")
        lines.append("| | rank | score | band | hand role | wrong role |")
        lines.append("|---|---|---|---|---|---|")
        for name in ("before", "after"):
            for rank, h in enumerate(case[name], 1):
                lines.append(f"| {name} | {rank} | {h['score']:.3f} | {h['confidence']} | "
                             f"{h['hand_role']} | {'yes' if h['mismatch'] else ''} |")
        lines.append("")
    return "\n".join(lines)


def unlabeled_pool(res: dict) -> list[str]:
    """Assets in any system's top 5 (gold or demo) that lack a hand role label."""
    labels = load_role_labels()
    pool = []
    for s in res["systems"].values():
        for pq in s.get("per_query", []):
            pool += pq["top5"]
    for case in res["demo_cases"]:
        pool += [h["asset"] for h in case["before"] + case["after"]]
    return sorted({a for a in pool if a not in labels})


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--pool", action="store_true",
                        help="print top-5 hits that have no hand role label, then exit")
    parser.add_argument("--no-write", action="store_true", help="print only")
    args = parser.parse_args()

    res = run_all()
    if args.pool:
        by_asset = {d["asset"]: d for d in corpus()}
        for asset in unlabeled_pool(res):
            text = " ".join(by_asset[asset]["text"].split())
            print(f"{asset}\t{text[:330]}")
        return

    md = to_markdown(res)
    print(md)
    if not args.no_write:
        RESULTS_DIR.mkdir(exist_ok=True)
        (RESULTS_DIR / "retrieval_eval.json").write_text(json.dumps(res, indent=2) + "\n")
        (RESULTS_DIR / "retrieval_eval.md").write_text(md + "\n")


if __name__ == "__main__":
    main()
