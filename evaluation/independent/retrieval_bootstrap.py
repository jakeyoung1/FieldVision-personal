"""Bootstrap 95% CIs for retrieval metrics (existing gold set + independent set).

    # 1) CIs on the repo's existing results (no recomputation, reads per-query rows)
    python retrieval_bootstrap.py [--write out.json] existing --results /path/to/fieldvision/evaluation/results/retrieval_eval.json

    # 2) Score the independent set once Jake has filled labeling_sheet.csv
    python retrieval_bootstrap.py independent

    # 3) Recall and wrong-role rates for the same set (retrieval_bootstrap_extra.json)
    python retrieval_bootstrap.py extra

In the repo the labeling sheet is local only (it quotes the notes). Both modes
then read labels_public.csv, which holds the same labels without the text, so
the committed result files can be rebuilt from committed inputs:

    python -m evaluation.independent.retrieval_bootstrap --write out.json independent
    python -m evaluation.independent.retrieval_bootstrap --write out.json extra

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
# The full sheet quotes the notes and is gitignored; the public copy has the
# same labels by query_id and asset. Use whichever exists, full sheet first.
SHEET = HERE / "labeling_sheet.csv"
if not SHEET.exists():
    SHEET = HERE / "labels_public.csv"
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


# ── mode 3: recall and wrong-role rates (retrieval_bootstrap_extra.json) ────
EXTRA_DEFINITIONS = {
    "recall_at_5": "relevant (label>=1) in top 5 / all relevant (label>=1) among the query's labeled "
                   "candidate pool; queries with zero relevant candidates excluded",
    "recall_at_5_strict": "same, relevant = label 2 only",
    "wrong_role_at_5": "share of the top 5 whose doc_role_rule_based != query_role "
                       "(mixed and unknown count as differing)",
    "wrong_role_at_5_opposite_only": "sensitivity: only pitcher-vs-hitter mismatches count; "
                                     "mixed/unknown not counted as wrong",
}
EXTRA_BOOTSTRAP = ("resample queries with replacement; paired difference resamples the same queries "
                   "for both systems; percentile 95% CI")


def extra(sheet: Path, key: Path) -> dict:
    """Recall@5 (both thresholds) and wrong-role rate@5 (both definitions).

    Written 2026-11-14 to rebuild retrieval_bootstrap_extra.json, which was
    computed by a one-off and had no script. Same seed, draws, and bootstrap
    as the other modes. Recall is per query and leaves out queries with no
    relevant candidate, so the paired difference uses only queries that have
    a value for both systems.
    """
    labels, query_role, rows = {}, {}, 0
    with sheet.open(newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            rows += 1
            v = r["relevance"].strip()
            if v not in ("0", "1", "2"):
                raise ValueError(f"extra needs every pair labeled 0, 1, or 2; {r['query_id']}/{r['asset']} is {v!r}")
            labels[(r["query_id"], r["asset"])] = int(v)
            query_role[r["query_id"]] = r["query_role"]
    doc_role, ranks = {}, {}
    with key.open(newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            doc_role[(r["query_id"], r["asset"])] = r["doc_role_rule_based"]
            for system in ("production", "bm25"):
                if r[f"{system}_rank"]:
                    ranks.setdefault(system, {}).setdefault(r["query_id"], []).append((int(r[f"{system}_rank"]), r["asset"]))
    qids = sorted(query_role)
    per = {}
    for system, by_q in ranks.items():
        m = {name: {} for name in EXTRA_DEFINITIONS}
        for q in qids:
            top = [a for _, a in sorted(by_q.get(q, []))][:K]
            for name, threshold in (("recall_at_5", 1), ("recall_at_5_strict", 2)):
                relevant = {a for (qq, a), v in labels.items() if qq == q and v >= threshold}
                if relevant:
                    m[name][q] = sum(a in relevant for a in top) / len(relevant)
            m["wrong_role_at_5"][q] = sum(doc_role[(q, a)] != query_role[q] for a in top) / K
            m["wrong_role_at_5_opposite_only"][q] = sum(
                {doc_role[(q, a)], query_role[q]} == {"pitcher", "hitter"} for a in top) / K
        per[system] = m
    out = {"source_sheet": f"{sheet.name} ({rows} rows, labels unchanged)", "draws": DRAWS, "seed": SEED, "k": K,
           "definitions": EXTRA_DEFINITIONS, "bootstrap": EXTRA_BOOTSTRAP,
           "systems": {}, "paired_production_minus_bm25": {}, "excluded_queries": {}}
    for system in ("production", "bm25"):
        out["systems"][system] = {name: {"mean": round(sum(v.values()) / len(v), 4), "ci95": ci(list(v.values())),
                                         "n_queries": len(v)}
                                  for name, v in per[system].items()}
    for name in EXTRA_DEFINITIONS:
        both = [q for q in qids if q in per["production"][name] and q in per["bm25"][name]]
        a = [per["production"][name][q] for q in both]
        b = [per["bm25"][name][q] for q in both]
        out["paired_production_minus_bm25"][name] = {"mean_diff": round(sum(x - y for x, y in zip(a, b)) / len(a), 4),
                                                     "ci95": paired_ci(a, b), "n_queries": len(both)}
        left_out = [q for q in qids if q not in both]
        if left_out:
            out["excluded_queries"][name] = left_out
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="mode", required=True)
    e = sub.add_parser("existing")
    e.add_argument("--results", required=True)
    for mode in ("independent", "extra"):
        m = sub.add_parser(mode)
        m.add_argument("--sheet", default=str(SHEET))
        m.add_argument("--key", default=str(HERE / "candidates_key.csv"))
    ap.add_argument("--write", help="write JSON to this path")
    a = ap.parse_args()
    if a.mode == "existing":
        r = existing(Path(a.results))
    else:
        r = (independent if a.mode == "independent" else extra)(Path(a.sheet), Path(a.key))
    text = json.dumps(r, indent=2)
    print(text)
    if a.write:
        Path(a.write).write_text(text + "\n")


if __name__ == "__main__":
    main()
