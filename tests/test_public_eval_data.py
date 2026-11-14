"""The public evaluation data: what is committed, what must never be, and what /methods shows.

Text written inside these tests ("TOY ...") is made up to exercise the guard.
It is not scouting text from any source.
"""
import csv
import importlib
import json
import re
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.main import app
from backend.services import audit
from evaluation import public_guard as guard

ROOT = Path(__file__).parent.parent
BENCH = ROOT / "evaluation" / "pro_benchmark"
INDEP = ROOT / "evaluation" / "independent"
client = TestClient(app)

TOY_PROSE = ("TOY TEXT, NOT A REAL REPORT. " + "This made-up sentence stands in for a paragraph of scouting "
             "prose so the guard has something long enough to notice. ") * 3


@pytest.fixture(autouse=True)
def temp_db(tmp_path, monkeypatch):
    monkeypatch.setenv("FV_AUDIT_DB", str(tmp_path / "audit.db"))
    importlib.reload(audit)
    yield


def _git(*args):
    return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True)


# ── What is committed ────────────────────────────────────────────────────────
def test_public_files_are_present_and_well_formed():
    bench = json.loads((BENCH / "pro_benchmark.public.json").read_text())
    with (BENCH / "pro_benchmark.public.csv").open(newline="", encoding="utf-8-sig") as f:
        bench_rows = list(csv.DictReader(f))
    assert len(bench["players"]) == len(bench_rows) == 96
    assert [p["id"] for p in bench["players"]] == [r["id"] for r in bench_rows]
    queries = json.loads((INDEP / "queries.public.json").read_text())["queries"]
    with (INDEP / "labels_public.csv").open(newline="", encoding="utf-8") as f:
        labels = list(csv.DictReader(f))
    assert len(queries) == 20
    assert {r["query_id"] for r in labels} == {q["id"] for q in queries}
    assert {r["relevance"] for r in labels} <= {"0", "1", "2"}        # every pair labeled, none blank
    assert all(r["labeler"] and r["labeled_on"] for r in labels)
    for name in ("retrieval_bootstrap_results.json", "retrieval_bootstrap_extra.json"):
        assert json.loads((INDEP / name).read_text())["draws"] > 0


def test_committed_public_files_carry_no_scouting_text():
    for folder in (BENCH, INDEP):
        for path in sorted(folder.iterdir()):
            rel = path.relative_to(ROOT).as_posix()
            if path.suffix not in (".csv", ".json") or rel in guard.FULL_FILES:
                continue
            assert guard.file_problems(path, rel) == [], rel


# ── What must never be ───────────────────────────────────────────────────────
def test_nothing_tracked_or_staged_in_the_public_folders_carries_excerpts():
    """Fails the moment an excerpt-bearing file is `git add`ed under the public eval paths."""
    if _git("rev-parse", "--is-inside-work-tree").returncode != 0:
        pytest.skip("not a git checkout")
    assert guard.check() == {}


def test_full_files_are_gitignored():
    if _git("rev-parse", "--is-inside-work-tree").returncode != 0:
        pytest.skip("not a git checkout")
    for rel in sorted(guard.FULL_FILES):
        assert _git("check-ignore", "-q", rel).returncode == 0, f"{rel} is not ignored"
        assert _git("ls-files", "--error-unmatch", rel).returncode != 0, f"{rel} is tracked"
    assert _git("check-ignore", "-q", "evaluation/pro_benchmark/anything.local.json").returncode == 0
    for rel in ("evaluation/pro_benchmark/pro_benchmark.public.csv", "evaluation/independent/labels_public.csv"):
        assert _git("check-ignore", "-q", rel).returncode != 0, f"{rel} should not be ignored"


@pytest.mark.parametrize("column", ["excerpt", "note_for_app", "query_note", "excerpt_verbatim", "note"])
def test_guard_flags_a_csv_with_a_text_column(tmp_path, column):
    path = tmp_path / "sheet.csv"
    with path.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["id", column])
        w.writerow(["x1", "TOY short text"])
    assert any(column in p for p in guard.file_problems(path, "evaluation/independent/sheet.csv"))


def test_guard_flags_json_with_a_text_key_at_any_depth(tmp_path):
    path = tmp_path / "data.json"
    path.write_text(json.dumps({"_meta": {"what": "toy"}, "players": [{"id": "x1", "note_for_app": "TOY"}]}))
    problems = guard.file_problems(path, "evaluation/pro_benchmark/data.json")
    assert any("note_for_app" in p for p in problems)


def test_guard_flags_prose_hidden_under_an_innocent_name(tmp_path):
    """Renaming the column does not get scouting text past the guard."""
    path = tmp_path / "data.csv"
    with path.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["id", "summary_line"])
        w.writerow(["x1", TOY_PROSE])
    assert any("reads like prose" in p for p in guard.file_problems(path, "evaluation/independent/data.csv"))
    as_json = tmp_path / "data.json"
    as_json.write_text(json.dumps({"rows": [{"id": "x1", "summary_line": TOY_PROSE}]}))
    assert any("reads like prose" in p for p in guard.file_problems(as_json, "evaluation/independent/data.json"))


def test_guard_flags_full_and_local_file_names(tmp_path):
    path = tmp_path / "pro_benchmark.json"
    path.write_text(json.dumps({"players": []}))
    assert guard.file_problems(path, "evaluation/pro_benchmark/pro_benchmark.json")
    assert guard.file_problems(path, "evaluation/pro_benchmark/demo_inputs.local.json")


def test_guard_allows_grades_links_and_our_own_explanations(tmp_path):
    path = tmp_path / "ok.json"
    path.write_text(json.dumps({
        "_meta": {"what": TOY_PROSE},                       # our description of the data
        "definitions": {"recall_at_5": TOY_PROSE},          # our metric definitions
        "players": [{"id": "x1", "published_grades_line": "Scouting grades: Hit: 50 | Power: 55 | Overall: 50",
                     "source_url": "https://example.test/" + "a" * 300}],
    }))
    assert guard.file_problems(path, "evaluation/pro_benchmark/ok.json") == []


def test_excerpt_scan_finds_a_stretch_of_protected_text(tmp_path):
    protected = "toy protected sentence number one about a fictional pitcher and his fictional slider"
    windows = {guard._normalize(protected)[i:i + guard.WINDOW] for i in range(0, len(protected) - guard.WINDOW, 10)}
    leak = tmp_path / "leak.csv"
    leak.write_text("id,comment\nx1,\"see: TOY protected   sentence number one about a fictional pitcher\"\n")
    clean = tmp_path / "clean.csv"
    clean.write_text("id,grade\nx1,55\n")
    assert guard.excerpt_hits(leak, windows) > 0
    assert guard.excerpt_hits(clean, windows) == 0


def test_local_full_files_have_no_player_name_in_the_note():
    """pb030's note kept the surname because the source lists it as 'Colleran Jr.'.
    Runs where the full file exists (never in CI); it must hold before the benchmark is run."""
    full = BENCH / "pro_benchmark.json"
    if not full.exists():
        pytest.skip("full benchmark file is local only")
    suffixes = {"jr", "jr.", "sr", "sr.", "ii", "iii", "iv"}
    leaks = []
    for p in json.loads(full.read_text())["players"]:
        parts = [t for t in p["player"].split() if t.lower() not in suffixes and len(t) > 2]
        if any(re.search(rf"\b{re.escape(t)}\b", p["note_for_app"]) for t in parts):
            leaks.append(p["id"])
    assert leaks == []


# ── What /methods shows ──────────────────────────────────────────────────────
def test_methods_passes_the_committed_results_through_unchanged():
    ind = client.get("/api/methods").json()["independent"]
    assert ind["results"] == json.loads((INDEP / "retrieval_bootstrap_results.json").read_text())
    assert ind["extra"] == json.loads((INDEP / "retrieval_bootstrap_extra.json").read_text())


def test_methods_counts_come_from_the_label_and_query_files():
    ind = client.get("/api/methods").json()["independent"]
    with (INDEP / "labels_public.csv").open(newline="", encoding="utf-8") as f:
        labels = list(csv.DictReader(f))
    queries = json.loads((INDEP / "queries.public.json").read_text())["queries"]
    assert ind["labels"]["pairs"] == len(labels)
    assert ind["labels"]["notes"] == ind["notes"]["count"] == len(queries)
    assert sum(ind["labels"]["by_relevance"].values()) == len(labels)
    assert ind["labels"]["labelers"] == sorted({r["labeler"] for r in labels})
    assert ind["notes"]["pitchers"] + ind["notes"]["position_players"] == len(queries)
    assert sum(s["notes"] for s in ind["notes"]["sources"]) == len(queries)
    assert ind["results"]["queries_fully_labeled"] == len(queries)      # results cover every note


def test_methods_page_renders_the_independent_test_from_data_only():
    html = (ROOT / "methods.html").read_text()
    assert 'id="independent"' in html and 'id="independent-table"' in html
    for key in ("precision_at_5", "precision_at_5_strict", "recall_at_5", "recall_at_5_strict", "mrr_at_10",
                "ndcg_at_10", "wrong_role_at_5", "wrong_role_at_5_opposite_only"):
        assert f"'{key}'" in html, key
    # No result is typed into the page. Every value in the two files must be absent from the HTML.
    typed = []
    for name in ("retrieval_bootstrap_results.json", "retrieval_bootstrap_extra.json"):
        data = json.loads((INDEP / name).read_text())
        for section in (data["systems"]["production"], data["systems"]["bm25"], data["paired_production_minus_bm25"]):
            for metric in section.values():
                for value in [metric.get("mean"), metric.get("mean_diff"), *metric["ci95"]]:
                    if value is None:        # a row has a mean or a mean_diff, not both
                        continue
                    text = f"{abs(value):.4f}".rstrip("0")
                    if len(text) >= 5 and text in html:          # 0.21 or 1.0 would match unrelated CSS
                        typed.append((name, text))
    assert typed == []
    pairs = sum(1 for _ in (INDEP / "labels_public.csv").open()) - 1
    assert str(pairs) not in html


def test_methods_states_the_limits_of_the_independent_test():
    html = (ROOT / "methods.html").read_text()
    section = html[html.index('id="independent"'):html.index("Grades: does the model grade like a coach?")]
    for phrase in ("one labeler", "not a college staff's own notes", "recall is measured against that pool",
                   "not published here"):
        assert phrase in section, phrase
    assert "One labeler, one source." in html[html.index('id="limitations"'):]
    assert "circular by construction" in html
