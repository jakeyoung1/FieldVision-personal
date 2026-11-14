"""GET /methods (page) and GET /api/methods (the numbers it shows).

The page states nothing numeric on its own. Every evaluation figure, corpus
count, and benchmark comes from this endpoint, which reads the committed
evaluation output and the data files directly, so the page cannot drift from
what the evaluations actually produced.
"""
import csv
import json
import re
from collections import Counter
from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import FileResponse, JSONResponse

from backend.services import mlb_benchmarks, rag, timing

ROOT = Path(__file__).resolve().parents[2]
RESULTS = ROOT / "evaluation" / "results"
BASKETBALL = ROOT / "data" / "basketball"
INDEPENDENT = ROOT / "evaluation" / "independent"
PRO_BENCHMARK = ROOT / "evaluation" / "pro_benchmark"
PAGE = ROOT / "methods.html"

router = APIRouter()


def _read(name: str) -> dict | None:
    path = RESULTS / name
    return json.loads(path.read_text()) if path.exists() else None


def _strip_per_query(retrieval: dict | None) -> dict | None:
    """The page needs the summary rows, not 37 queries of detail."""
    if not retrieval:
        return None
    out = dict(retrieval)
    out["systems"] = {name: {k: v for k, v in s.items() if k != "per_query"}
                      for name, s in retrieval["systems"].items()}
    return out


def _corpus() -> dict:
    docs = rag._load_index(True)[2]
    years = Counter(d["year"] or "Undated" for d in docs)
    dated = sorted(int(y) for y in years if y != "Undated")
    return {"documents": len(docs), "by_year": dict(sorted(years.items())),
            "first_year": dated[0], "last_year": dated[-1]}


def _basketball() -> dict:
    seasons = {}
    for f in sorted(BASKETBALL.glob("bbref_*.csv")):
        end_year = int(f.stem.split("_")[1])
        with f.open() as fh:
            seasons[f"{end_year - 1}-{str(end_year)[-2:]}"] = sum(1 for _ in fh) - 1
    return {"seasons": seasons, "player_seasons": sum(seasons.values())}


def _decision_mapping() -> dict | None:
    """Agreement between the two AI runs on the app's four labels and on the
    three decisions they are folded into, so the folding can be checked."""
    from evaluation import grade_validation as G
    first, second = G.load_ai(), G.load_ai(G.AI_REPEAT)
    ids = sorted(set(first) & set(second))
    if not ids:
        return None
    pairs = [(first[i]["recommendation"], second[i]["recommendation"]) for i in ids]
    return {
        "players": len(ids),
        "four_way_same": sum(a == b for a, b in pairs),
        "three_way_same": sum(G.DECISION[a] == G.DECISION[b] for a, b in pairs),
        "mapping": G.DECISION,
    }


def _candidates(labels: list[dict]) -> dict | None:
    """What the pooled candidates show beyond the bootstrap files.

    Read from candidates_key.csv (each system's rank and score per report) and
    the labels. Two findings come from here: how FieldVision's matches on real
    notes sit against the app's confidence cutoffs, and the gaps in BM25's
    ranks where a duplicate transcription was dropped.
    """
    path = INDEPENDENT / "candidates_key.csv"
    if not path.exists():
        return None
    with path.open(newline="", encoding="utf-8") as f:
        key = list(csv.DictReader(f))
    label = {(r["query_id"], r["asset"]): int(r["relevance"]) for r in labels if r["relevance"].strip()}
    hits = [r for r in key if r["production_rank"]]
    # Bands are recomputed with the app's current cutoffs, not read from the
    # key, so the page always describes the thresholds the app actually uses.
    bands = Counter(rag.confidence_band(float(r["production_score"])) for r in hits)
    relevant = [r for r in hits if label.get((r["query_id"], r["asset"]), 0) >= 1]
    bm25: dict[str, set[int]] = {}
    for r in key:
        if r["bm25_rank"]:
            bm25.setdefault(r["query_id"], set()).add(int(r["bm25_rank"]))
    depth = max((int(r["production_rank"]) for r in hits), default=0)
    gaps = {q: sorted(set(range(1, depth + 1)) - ranks) for q, ranks in bm25.items() if len(ranks) < depth}
    return {
        "depth": depth,
        "production": {
            "hits": len(hits),
            "notes": len({r["query_id"] for r in hits}),
            "by_band": {band: bands.get(band, 0) for band in ("strong", "moderate", "weak")},
            "highest_score": round(max(float(r["production_score"]) for r in hits), 4) if hits else None,
            "labeled_relevant": len(relevant),
            "labeled_clearly_relevant": sum(label[(r["query_id"], r["asset"])] == 2 for r in relevant),
            "notes_with_a_relevant_hit": len({r["query_id"] for r in relevant}),
        },
        "cutoffs": {"usable_from": rag.WEAK_SCORE, "strong_from": rag.STRONG_SCORE},
        "bm25_gaps": {
            "notes": len(gaps),
            "total": sum(len(g) for g in gaps.values()),
            "in_top_5": sum(rank <= 5 for g in gaps.values() for rank in g),
            # Established by rerunning BM25 locally on the notes, which are not in
            # the repo; tests/test_independent_reproducible.py pins the counts.
            "cause": "duplicate transcriptions",
            "checked_on": "2026-11-14",
        },
    }


def _independent() -> dict | None:
    """The independent matching test, exactly as committed.

    `results` and `extra` are the two bootstrap files passed through unchanged.
    The counts beside them (pairs, labeler, dates, notes by source) are read
    from the label and query files, so nothing about this test is typed into
    the page by hand. The notes' text is not in the repo; only links are.
    """
    files = {name: INDEPENDENT / name for name in (
        "retrieval_bootstrap_results.json", "retrieval_bootstrap_extra.json",
        "queries.public.json", "labels_public.csv")}
    if not all(path.exists() for path in files.values()):
        return None
    queries = json.loads(files["queries.public.json"].read_text())
    with files["labels_public.csv"].open(newline="", encoding="utf-8") as f:
        labels = list(csv.DictReader(f))
    by_source: dict[tuple[str, str], int] = Counter(
        (q["source"], q["source_url"]) for q in queries["queries"])
    return {
        "candidates": _candidates(labels),
        "results": json.loads(files["retrieval_bootstrap_results.json"].read_text()),
        "extra": json.loads(files["retrieval_bootstrap_extra.json"].read_text()),
        "labels": {
            "pairs": len(labels),
            "notes": len({r["query_id"] for r in labels}),
            "labelers": sorted({r["labeler"] for r in labels if r["labeler"]}),
            "labeled_on": sorted({r["labeled_on"] for r in labels if r["labeled_on"]}),
            "by_relevance": dict(sorted(Counter(r["relevance"] for r in labels).items())),
        },
        "notes": {
            "count": len(queries["queries"]),
            "pitchers": sum(q["role"] == rag.ROLE_PITCHER for q in queries["queries"]),
            "position_players": sum(q["role"] == rag.ROLE_HITTER for q in queries["queries"]),
            "written_from_rickey_reports": sum(bool(q["written_from_rickey_report"]) for q in queries["queries"]),
            "date_checked": queries["_meta"]["date_checked"],
            "sources": [{"source": source, "url": url, "notes": n}
                        for (source, url), n in sorted(by_source.items())],
        },
    }


def _pro_benchmark() -> dict | None:
    """The pro benchmark: FieldVision's grades against published 20-80 grades.

    `dev`, `test`, and `test_no_grade_words` are the three committed result
    files passed through unchanged. `context` holds what the page needs to
    describe them (how many players carry the grade-word flag, the range of
    published grades, where the letter scale tops out, how the run went), read
    from the committed benchmark, scorer, and run record. `calibration` is the
    committed result file for the calibrated overall. Nothing is computed here
    that changes a result.
    """
    files = {"dev": "benchmark_dev_results.json", "test": "benchmark_test_results.json",
             "test_no_grade_words": "benchmark_test_no_grade_words_results.json"}
    public = PRO_BENCHMARK / "pro_benchmark.public.csv"
    if not public.exists() or not all((PRO_BENCHMARK / name).exists() for name in files.values()):
        return None
    from evaluation.pro_benchmark import score_pro_benchmark as scorer

    with public.open(newline="", encoding="utf-8-sig") as f:
        players = list(csv.DictReader(f))
    grade_columns = [c for c in players[0] if c.startswith("pub_")]

    def split_context(split: str) -> dict:
        rows = [r for r in players if r["split"] == split]
        published = [float(r[c]) for r in rows for c in grade_columns if r[c].strip()]
        overall = [float(r["pub_overall"]) for r in rows if r["pub_overall"].strip()]
        excerpt = sorted(int(r["excerpt_chars"]) for r in rows if r["excerpt_chars"].strip())
        return {
            "players": len(rows),
            "pitchers": sum(r["role"] == rag.ROLE_PITCHER for r in rows),
            "position_players": sum(r["role"] == rag.ROLE_HITTER for r in rows),
            "with_grade_words": sum(r["excerpt_has_grade_words"] == "True" for r in rows),
            "published_overall_lowest": min(overall), "published_overall_highest": max(overall),
            "published_highest_any_tool": max(published),
            "published_above_letter_cap": sum(v > max(scorer.LETTER_TO_2080.values()) for v in published),
            "excerpt_chars_median": excerpt[len(excerpt) // 2], "excerpt_chars_longest": excerpt[-1],
        }

    run_path = PRO_BENCHMARK / "benchmark_run_results.json"
    run = json.loads(run_path.read_text()) if run_path.exists() else {}
    # The calibrated overall (backend/services/pro_scale.py), scored by
    # evaluation/pro_benchmark/score_pro_scale.py. Passed through unchanged.
    calibration_path = PRO_BENCHMARK / "benchmark_pro_scale_results.json"
    return {
        **{key: json.loads((PRO_BENCHMARK / name).read_text()) for key, name in files.items()},
        "calibration": json.loads(calibration_path.read_text()) if calibration_path.exists() else None,
        "context": {
            "splits": {split: split_context(split) for split in ("dev", "test")},
            # "MLB Pipeline 2024 Draft Top 250, pre-draft report" -> "MLB Pipeline"
            "sources": sorted({re.split(r"\s+\d{4}\b", r["source"])[0] for r in players}),
            "draft_years": sorted({r["draft_year"] for r in players}),
            "date_checked": sorted({r["date_checked"] for r in players})[-1],
            "letter_scale": {"highest": max(scorer.LETTER_TO_2080.values()),
                             "lowest": min(scorer.LETTER_TO_2080.values()), "step": scorer.STEP},
            "run": {k: run.get(k) for k in ("players_requested", "players_successful", "api_attempts", "retries")}
                   | {k: run.get("_meta", {}).get(k) for k in ("model", "trackman")},
        },
    }


def methods_data() -> dict:
    return {
        "retrieval": _strip_per_query(_read("retrieval_eval.json")),
        "grades": _read("grade_validation.json"),
        "corpus": _corpus(),
        "benchmarks": mlb_benchmarks.describe(),
        "basketball": _basketball(),
        "independent": _independent(),
        "pro_benchmark": _pro_benchmark(),
        "decision_mapping": _decision_mapping(),
        "matching_characters": rag.QUERY_CHARS,
        "time_study": _read("time_study.json"),
        "app_timings": timing.summary(),
    }


@router.get("/api/methods")
def api_methods():
    return JSONResponse(methods_data())


@router.get("/methods", include_in_schema=False)
def methods_page():
    return FileResponse(str(PAGE))
