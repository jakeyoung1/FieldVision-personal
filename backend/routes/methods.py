"""GET /methods (page) and GET /api/methods (the numbers it shows).

The page states nothing numeric on its own. Every evaluation figure, corpus
count, and benchmark comes from this endpoint, which reads the committed
evaluation output and the data files directly, so the page cannot drift from
what the evaluations actually produced.
"""
import csv
import json
from collections import Counter
from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import FileResponse, JSONResponse

from backend.services import mlb_benchmarks, rag, timing

ROOT = Path(__file__).resolve().parents[2]
RESULTS = ROOT / "evaluation" / "results"
BASKETBALL = ROOT / "data" / "basketball"
INDEPENDENT = ROOT / "evaluation" / "independent"
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


def methods_data() -> dict:
    return {
        "retrieval": _strip_per_query(_read("retrieval_eval.json")),
        "grades": _read("grade_validation.json"),
        "corpus": _corpus(),
        "benchmarks": mlb_benchmarks.describe(),
        "basketball": _basketball(),
        "independent": _independent(),
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
