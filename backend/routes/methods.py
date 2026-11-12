"""GET /methods (page) and GET /api/methods (the numbers it shows).

The page states nothing numeric on its own. Every evaluation figure, corpus
count, and benchmark comes from this endpoint, which reads the committed
evaluation output and the data files directly, so the page cannot drift from
what the evaluations actually produced.
"""
import json
from collections import Counter
from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import FileResponse, JSONResponse

from backend.services import mlb_benchmarks, rag

ROOT = Path(__file__).resolve().parents[2]
RESULTS = ROOT / "evaluation" / "results"
BASKETBALL = ROOT / "data" / "basketball"
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


def methods_data() -> dict:
    return {
        "retrieval": _strip_per_query(_read("retrieval_eval.json")),
        "grades": _read("grade_validation.json"),
        "corpus": _corpus(),
        "benchmarks": mlb_benchmarks.describe(),
        "basketball": _basketball(),
    }


@router.get("/api/methods")
def api_methods():
    return JSONResponse(methods_data())


@router.get("/methods", include_in_schema=False)
def methods_page():
    return FileResponse(str(PAGE))
