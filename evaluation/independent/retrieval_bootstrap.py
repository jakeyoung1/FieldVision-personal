"""Bootstrap 95% CIs for retrieval metrics (existing gold set + independent set).

    # 1) CIs on the repo's existing results (no recomputation, reads per-query rows)
    python retrieval_bootstrap.py [--write out.json] existing --results /path/to/fieldvision/evaluation/results/retrieval_eval.json

    # 2) Score the independent set once Jake has filled labeling_sheet.csv
    python retrieval_bootstrap.py independent

Relevance scale for labeling_sheet.csv (column `relevance`):
  2 = clearly relevant: the Rickey report evaluates a player with the same
      role and the same headline tools/traits as the modern note
  1 = partly relevant: same role, overlaps on at least one described tool
  0 = not relevant (other role, no shared tools, or not a player evaluation)
Primary metrics treat relevance >= 1 as a hit; "strict" treats only 2 as a hit.
Only queries whose candidates are ALL labeled are scored. Unlabeled -> no number.

Bootstrap: resample QUERIES with replacement, 2,000 draws, seed 20261112.
Paired differences (production - bm25) resample the same queries for both.
"""
import argparse
import csv
import json
import math
import random
from pathlib import Path

HERE = Path(__file__).resolve().parent
SEED = 20261112
DRAWS = 2000
K = 5
DEPTH = 10


def ci(values, draws=DRAWS, seed=SEED):
    n = len(values)
    if n < 2:
        return None
    rng = random.Random(seed)
    means = sorted(sum(values[rng.randrange(n)] for _ in range(n)) / n for _ in range(draws))
    return [round(means[int(0.025 * draws)], 4), round(means[int(0.975 * draws) - 1], 4)]


def paired_ci(a, b, draws=DRAWS, seed=SEED):
    return ci([x - y for x, y in zip(a, b)], draws, seed)


def summarize(per_metric: dict[str, list[float]]) -> dict:
    return {m: {"mean": round(sum(v) / len(v), 4) if v else None, "ci95": ci(v), "n_queries": len(v)}
            for m, v in per_metric.items()}


# ── mode 1: existing gold set ───────────────────────────────────────────────
def existing(results_path: Path) -> dict:
    res = json.loads(results_path.read_text())
    out = {"source": str(results_path), "draws": DRAWS, "seed": SEED, "systems": {}, "paired_vs_bm25": {}}
    per = {}
    for name, s in res["systems"].items():
        rows = s.get("per_query")
        if not rows:
            continue   # random baseline stores only averages
        per[name] = {"precision_at_5": [r["precision_at_5"] for r in rows],
                     "recall_at_5": [r["recall_at_5"] for r in rows],
                     "mrr_at_10": [r["rr"] for r in rows]}
        out["systems"][name] = summarize(per[name])
    if "bm25" in per:
        for name, m in per.items():
            if name == "bm25":
                continue
            out["paired_vs_bm25"][name] = {k: {"mean_diff": round(sum(a - b for a, b in zip(m[k], per["bm25"][k])) / len(m[k]), 4),
                                               "ci95": paired_ci(m[k], per["bm25"][k])} for k in m}
    return out


# ── mode 2: independent set ─────────────────────────────────────────────────
def _dcg(gains):
    return sum(g / math.log2(i + 2) for i, g in enumerate(gains))


def independent(sheet: Path, key: Path) -> dict:
    labels = {}
    with sheet.open(newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            v = r["relevance"].strip()
            labels[(r["query_id"], r["asset"])] = int(v) if v else None
    ranks = {}
    with key.open(newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            for system in ("production", "bm25"):
                if r[f"{system}_rank"]:
                    ranks.setdefault(system, {}).setdefault(r["query_id"], []).append((int(r[f"{system}_rank"]), r["asset"]))
    qids = sorted({q for q, _ in labels})
    complete = [q for q in qids if all(v is not None for (qq, _), v in labels.items() if qq == q)]
    for v in labels.values():
        if v is not None and v not in (0, 1, 2):
            raise ValueError(f"relevance must be 0, 1, or 2; got {v}")
    out = {"queries_total": len(qids), "queries_fully_labeled": len(complete),
           "draws": DRAWS, "seed": SEED, "systems": {}, "paired_production_minus_bm25": {}}
    if not complete:
        out["note"] = "No query is fully labeled yet. Fill `relevance` in labeling_sheet.csv."
        return out
    per = {}
    for system, by_q in ranks.items():
        m = {"precision_at_5": [], "precision_at_5_strict": [], "mrr_at_10": [], "ndcg_at_10": [],
             "any_relevant_in_top10": []}
        for q in complete:
            ranked = [a for _, a in sorted(by_q.get(q, []))][:DEPTH]
            gains = [labels[(q, a)] for a in ranked]
            ideal = sorted([v for (qq, _), v in labels.items() if qq == q], reverse=True)[:DEPTH]
            m["precision_at_5"].append(sum(g >= 1 for g in gains[:K]) / K)
            m["precision_at_5_strict"].append(sum(g == 2 for g in gains[:K]) / K)
            m["mrr_at_10"].append(next((1 / (i + 1) for i, g in enumerate(gains) if g >= 1), 0.0))
            m["ndcg_at_10"].append(_dcg(gains) / _dcg(ideal) if _dcg(ideal) else 0.0)
            m["any_relevant_in_top10"].append(float(any(g >= 1 for g in gains)))
        per[system] = m
        out["systems"][system] = summarize(m)
    if {"production", "bm25"} <= set(per):
        for k in per["production"]:
            a, b = per["production"][k], per["bm25"][k]
            out["paired_production_minus_bm25"][k] = {"mean_diff": round(sum(x - y for x, y in zip(a, b)) / len(a), 4),
                                                      "ci95": paired_ci(a, b)}
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="mode", required=True)
    e = sub.add_parser("existing")
    e.add_argument("--results", required=True)
    i = sub.add_parser("independent")
    i.add_argument("--sheet", default=str(HERE / "labeling_sheet.csv"))
    i.add_argument("--key", default=str(HERE / "candidates_key.csv"))
    ap.add_argument("--write", help="write JSON to this path")
    a = ap.parse_args()
    r = existing(Path(a.results)) if a.mode == "existing" else independent(Path(a.sheet), Path(a.key))
    text = json.dumps(r, indent=2)
    print(text)
    if a.write:
        Path(a.write).write_text(text + "\n")


if __name__ == "__main__":
    main()
