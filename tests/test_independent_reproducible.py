"""Both independent-test result files rebuild exactly from committed inputs.

The labels (labels_public.csv), the rankings (candidates_key.csv), and the
script (retrieval_bootstrap.py) are all in the repo, so CI can redo the
bootstrap and compare. If a label, a ranking, or the scoring code changes
without the result files being regenerated, this fails.
"""
import csv
import json
import subprocess
import sys
from pathlib import Path

import pytest

from evaluation.independent import retrieval_bootstrap as rb

ROOT = Path(__file__).parent.parent
HERE = rb.HERE
LABELS = HERE / "labels_public.csv"
KEY = HERE / "candidates_key.csv"


def _committed(name):
    return json.loads((HERE / name).read_text())


def test_results_file_rebuilds_from_the_public_labels():
    assert rb.independent(LABELS, KEY) == _committed("retrieval_bootstrap_results.json")


def test_extra_file_rebuilds_from_the_public_labels():
    rebuilt, committed = rb.extra(LABELS, KEY), _committed("retrieval_bootstrap_extra.json")
    # source_sheet names the input file. The committed copy was built from the
    # local labeling sheet; the labels in it are the same 329, by query and asset.
    assert rebuilt.pop("source_sheet") == "labels_public.csv (329 rows, labels unchanged)"
    assert committed.pop("source_sheet") == "labeling_sheet.csv (329 rows, labels unchanged)"
    assert rebuilt == committed


def test_the_command_line_writes_the_same_files(tmp_path):
    for mode, name in (("independent", "retrieval_bootstrap_results.json"), ("extra", "retrieval_bootstrap_extra.json")):
        out = tmp_path / f"{mode}.json"
        done = subprocess.run([sys.executable, "-m", "evaluation.independent.retrieval_bootstrap", "--write", str(out),
                               mode, "--sheet", str(LABELS), "--key", str(KEY)],
                              cwd=ROOT, capture_output=True, text=True, timeout=120)
        assert done.returncode == 0, done.stderr[-400:]
        rebuilt, committed = json.loads(out.read_text()), _committed(name)
        rebuilt.pop("source_sheet", None)
        committed.pop("source_sheet", None)
        assert rebuilt == committed, mode


def test_public_labels_match_the_local_sheet_where_it_exists():
    """The public file must carry exactly the labels Jake entered. Local only."""
    sheet = HERE / "labeling_sheet.csv"
    if not sheet.exists():
        pytest.skip("the full labeling sheet is local only")

    def read(path):
        with path.open(newline="", encoding="utf-8") as f:
            return {(r["query_id"], r["asset"]): r["relevance"] for r in csv.DictReader(f)}
    assert read(LABELS) == read(sheet)


def _ranks(key, system):
    per_query = {}
    for r in key:
        if r[f"{system}_rank"]:
            per_query.setdefault(r["query_id"], []).append(int(r[f"{system}_rank"]))
    return per_query


def test_candidates_key_covers_every_labeled_pair_and_ranks_both_systems():
    with KEY.open(newline="", encoding="utf-8") as f:
        key = list(csv.DictReader(f))
    with LABELS.open(newline="", encoding="utf-8") as f:
        labels = {(r["query_id"], r["asset"]) for r in csv.DictReader(f)}
    assert {(r["query_id"], r["asset"]) for r in key} == labels
    production, bm25 = _ranks(key, "production"), _ranks(key, "bm25")
    assert len(production) == len(bm25) == 20
    # FieldVision collapses duplicate transcriptions itself, so its top 10 is complete.
    assert all(sorted(v) == list(range(1, 11)) for v in production.values())
    for v in bm25.values():
        assert len(v) == len(set(v)) and set(v) <= set(range(1, 11))


def test_bm25_rank_gaps_are_the_dropped_duplicates():
    """BM25 does not collapse duplicate transcriptions. The candidate builder
    folded pages onto one id and kept each system's first rank, so a second copy
    of a page BM25 had already ranked was dropped, leaving a gap. Checked against
    a local BM25 rerun on 2026-11-14: all 11 gaps are such duplicates, 5 of them
    in the top 5. The scorer then moves BM25's later results up, which favors
    BM25 relative to the synthetic test, where a returned duplicate wastes a slot.
    Pinned so a rebuilt key cannot change this silently."""
    with KEY.open(newline="", encoding="utf-8") as f:
        bm25 = _ranks(list(csv.DictReader(f)), "bm25")
    gaps = {q: sorted(set(range(1, 11)) - set(v)) for q, v in bm25.items() if len(v) < 10}
    assert sum(len(g) for g in gaps.values()) == 11
    assert sum(r <= 5 for g in gaps.values() for r in g) == 5
    assert len(gaps) == 9


def test_extra_refuses_unlabeled_pairs(tmp_path):
    rows = list(csv.DictReader(LABELS.open(newline="", encoding="utf-8")))
    rows[0]["relevance"] = ""
    broken = tmp_path / "labels.csv"
    with broken.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    with pytest.raises(ValueError, match="every pair labeled"):
        rb.extra(broken, KEY)


# ── What /methods says about the candidates ─────────────────────────────────
def _methods_candidates(monkeypatch, tmp_path):
    import importlib

    from fastapi.testclient import TestClient

    from backend.main import app
    from backend.services import audit
    monkeypatch.setenv("FV_AUDIT_DB", str(tmp_path / "audit.db"))
    importlib.reload(audit)
    return TestClient(app).get("/api/methods").json()["independent"]["candidates"]


def test_methods_cutoff_figures_match_a_recount(monkeypatch, tmp_path):
    from backend.services import rag
    c = _methods_candidates(monkeypatch, tmp_path)
    with KEY.open(newline="", encoding="utf-8") as f:
        hits = [r for r in csv.DictReader(f) if r["production_rank"]]
    with LABELS.open(newline="", encoding="utf-8") as f:
        label = {(r["query_id"], r["asset"]): int(r["relevance"]) for r in csv.DictReader(f)}
    p = c["production"]
    assert p["hits"] == len(hits) == 200 and c["depth"] == 10
    scores = [float(r["production_score"]) for r in hits]
    assert p["highest_score"] == max(scores) < rag.WEAK_SCORE          # every match is below the usable line
    assert p["by_band"] == {"strong": 0, "moderate": 0, "weak": 200}
    assert p["labeled_relevant"] == sum(label[(r["query_id"], r["asset"])] >= 1 for r in hits)
    assert p["labeled_clearly_relevant"] == sum(label[(r["query_id"], r["asset"])] == 2 for r in hits)
    assert c["cutoffs"] == {"usable_from": rag.WEAK_SCORE, "strong_from": rag.STRONG_SCORE}


def test_bands_follow_the_apps_cutoffs_not_the_stored_column(monkeypatch, tmp_path):
    """If the cutoff moves, the page must describe the new one."""
    from backend.services import rag
    monkeypatch.setattr(rag, "WEAK_SCORE", 0.10)
    p = _methods_candidates(monkeypatch, tmp_path)["production"]
    assert p["by_band"]["moderate"] > 0 and sum(p["by_band"].values()) == 200


def test_methods_gap_figures_match_the_pinned_counts(monkeypatch, tmp_path):
    g = _methods_candidates(monkeypatch, tmp_path)["bm25_gaps"]
    assert (g["notes"], g["total"], g["in_top_5"]) == (9, 11, 5)
    assert g["cause"] == "duplicate transcriptions"


def test_methods_page_renders_both_findings_from_data():
    html = (ROOT / "methods.html").read_text()
    assert 'id="independent-cutoff"' in html and 'id="independent-duplicates"' in html
    script = html[html.index("const C = ind.candidates;"):html.index("// Plain-language versions")]
    for ref in ("P.hits", "P.highest_score", "P.labeled_relevant", "P.labeled_clearly_relevant",
                "cut.usable_from", "B.total", "B.in_top_5", "B.notes"):
        assert ref in script, ref
    for typed in ("107", "0.1439", "0.144", "200 of", "11 times"):
        assert typed not in html, typed
    limits = html[html.index('id="limitations"'):]
    assert "The match cutoff is tuned to synthetic notes." in limits
