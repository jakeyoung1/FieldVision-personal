"""/methods and the calibrated overall: rendered from one results file that rebuilds from the committed grades."""
import importlib
import json
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.main import app
from backend.services import audit, pro_scale
from evaluation.pro_benchmark import score_pro_scale

ROOT = Path(__file__).parent.parent
BENCH = ROOT / "evaluation" / "pro_benchmark"
RESULTS = BENCH / "benchmark_pro_scale_results.json"
HTML = (ROOT / "methods.html").read_text()
client = TestClient(app)


@pytest.fixture(autouse=True)
def temp_db(tmp_path, monkeypatch):
    monkeypatch.setenv("FV_AUDIT_DB", str(tmp_path / "audit.db"))
    importlib.reload(audit)
    yield


def _committed():
    return json.loads(RESULTS.read_text())


def _section():
    start = HTML.index('<h3 id="pro-benchmark">')
    return HTML[start:HTML.index("<h3", start + 10)]


def _script():
    start = HTML.index("// The calibrated overall.")
    return HTML[start:HTML.index("// Grades\n")]


def _floats(node):
    if isinstance(node, float):
        yield node
    elif isinstance(node, dict):
        for v in node.values():
            yield from _floats(v)
    elif isinstance(node, list):
        for v in node:
            yield from _floats(v)


# ── The results file ─────────────────────────────────────────────────────────
def test_results_file_rebuilds_from_the_committed_grades():
    """No model call and no hand edits: the script gives the committed file, byte for byte."""
    assert json.dumps(score_pro_scale.build(), indent=2) + "\n" == RESULTS.read_text()


def test_results_use_the_shipped_rule_and_say_it_is_frozen():
    meta = _committed()["_meta"]
    assert meta["rule"]["steps_down"] == pro_scale.STEPS_DOWN
    assert meta["rule"]["step_points"] == pro_scale.STEP
    assert (meta["rule"]["lowest"], meta["rule"]["highest"]) == (pro_scale.SCALE_MIN, pro_scale.SCALE_MAX)
    assert (meta["chosen_on"], meta["checked_on"], meta["model_calls"]) == ("dev", "test", 0)
    assert "may be used to adjust" in meta["frozen"]


@pytest.mark.parametrize("split", ["dev", "test"])
def test_before_row_is_the_committed_benchmark_overall_row(split):
    """'As graded' is the same number the benchmark section already shows."""
    benchmark = json.loads((BENCH / f"benchmark_{split}_results.json").read_text())["by_tool"]["overall"]
    assert _committed()[split]["before"] == benchmark


def test_results_are_the_numbers_validated_before_the_field_shipped():
    d = _committed()
    test, dev = d["test"], d["dev"]
    assert (test["after"]["mae"], test["after"]["bias"], test["after"]["within_step"]) == (5.0, 1.6667, 0.7292)
    assert test["after"]["ci95"]["bias"] == [-0.2083, 3.5417]
    assert test["paired_change"]["ci95"]["mae"] == [-6.5625, -3.4375]
    assert (dev["after"]["mae"], dev["after"]["bias"], dev["after"]["within_step"]) == (5.0, 0.2083, 0.75)
    assert len(test["conditions"]) == 6 and all(c["held"] for c in test["conditions"])
    assert {k: v["players"] for k, v in test["by_role"].items()} == {"pitcher": 24, "hitter": 24}
    why = d["why_two_step_sizes"]
    assert why["split"] == "dev"                                    # the reason was worked out on dev only
    assert why["published_overall_minus_published_tools"]["pitcher"]["mean"] < \
        why["published_overall_minus_published_tools"]["hitter"]["mean"] < 0


def test_facts_the_page_states_in_words_hold_in_the_data():
    """The page picks its sentences from these comparisons; this pins which ones it picks today."""
    test = _committed()["test"]
    low, high = test["after"]["ci95"]["bias"]
    assert low <= 0 <= high                                         # "no measurable lean" after calibration
    assert test["before"]["ci95"]["bias"][0] > 0                    # "runs high" as graded
    assert test["paired_change"]["ci95"]["mae"][1] < 0              # the average gap fell
    low, high = test["paired_change"]["ci95"]["spearman"]
    assert low <= 0 <= high                                         # ranking did not change
    assert test["same_grade_for_everyone"]["mae"] < test["after"]["mae"]   # one grade for everyone is still closer


# ── The API ──────────────────────────────────────────────────────────────────
def test_api_passes_the_results_file_through_unchanged():
    assert client.get("/api/methods").json()["pro_benchmark"]["calibration"] == _committed()


def test_api_reports_no_calibration_when_the_file_is_absent(monkeypatch, tmp_path):
    from backend.routes import methods
    for path in BENCH.iterdir():
        if path.is_file() and path.suffix in (".json", ".csv") and path.name != RESULTS.name:
            (tmp_path / path.name).write_bytes(path.read_bytes())
    monkeypatch.setattr(methods, "PRO_BENCHMARK", tmp_path)
    assert methods._pro_benchmark()["calibration"] is None


# ── The page ─────────────────────────────────────────────────────────────────
def test_calibration_block_sits_inside_the_benchmark_section_after_the_tables():
    section = _section()
    order = [section.index(f'id="{name}"') for name in
             ("bench-dev-table", "bench-limits", "pro-scale", "cal-intro", "cal-test-table", "cal-test-reading",
              "cal-role-table", "cal-dev-table", "cal-limits")]
    assert order == sorted(order)
    assert section.index('id="cal-test-table"') < section.index('id="cal-dev-table"')      # test before dev


def test_no_result_value_is_typed_into_the_calibration_block():
    block = _section()[_section().index('id="pro-scale"'):]
    assert re.findall(r"\d", re.sub(r"<[^>]+>", " ", block)) == [], "the static HTML must contain no digits"
    numbers = set(re.findall(r"(?<![\w.])\d+(?:\.\d+)?", _script()))
    assert numbers <= {"0", "1", "100"}, numbers                    # indexes, the singular test, and percent


def test_no_calibration_result_appears_anywhere_in_the_page():
    typed = []
    for value in set(_floats(_committed())):
        raw = f"{abs(value):.4f}"
        if len(raw.rstrip("0")) >= 6 and raw in HTML:               # 4-decimal values only; 5.0 could be CSS
            typed.append(raw)
    assert typed == []


def test_page_reads_the_calibration_from_the_data():
    script = _script()
    for needle in ("pb.calibration", "cal._meta.rule", "CT.conditions", "CT.paired_change", "CT.by_role",
                   "CT.same_grade_for_everyone", "why.players", "rule.step_points", "r.steps_down"):
        assert needle in script, needle
    for key in ("mae", "bias", "within_step", "spearman"):
        assert f"'{key}'" in script or f".{key}" in script, key


def test_page_says_what_the_calibration_is_and_is_not():
    script = _script()
    for phrase in ("It is not a second evaluation", "The letter and the tool grades are not changed",
                   "checked once on", "cannot be used to change them", "It does not make it more informative",
                   "nothing here has been checked against a coach's grades", "no calibrated number is shown",
                   "A scout's override of the overall letter does not change it",
                   "ranks players no better than the letter does"):
        assert phrase in script, phrase
    assert "bench-limit-overall').textContent +=" in script         # the Limitations entry mentions it too
