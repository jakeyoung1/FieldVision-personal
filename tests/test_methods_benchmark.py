"""The pro benchmark section of /methods: rendered from the committed result
files, with no result typed into the page."""
import csv
import importlib
import json
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.main import app
from backend.services import audit
from evaluation.pro_benchmark import score_pro_benchmark as scorer

ROOT = Path(__file__).parent.parent
BENCH = ROOT / "evaluation" / "pro_benchmark"
HTML = (ROOT / "methods.html").read_text()
FILES = {"dev": "benchmark_dev_results.json", "test": "benchmark_test_results.json",
         "test_no_grade_words": "benchmark_test_no_grade_words_results.json"}
# The rule the page describes in words, as the benchmark builder applied it.
FLAG_RULE = re.compile(r"\b[2-8]0\b|\b[2-8]5\b|plus-plus|\bplus\b|above-average|below-average|"
                       r"\baverage\b|\bfringy?\b", re.I)


@pytest.fixture
def benchmark(tmp_path, monkeypatch):
    monkeypatch.setenv("FV_AUDIT_DB", str(tmp_path / "audit.db"))
    importlib.reload(audit)
    return TestClient(app).get("/api/methods").json()["pro_benchmark"]


def _committed(key):
    return json.loads((BENCH / FILES[key]).read_text())


def _players():
    with (BENCH / "pro_benchmark.public.csv").open(newline="", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def _section():
    """The benchmark section's static HTML: from its heading to the next h3."""
    start = HTML.index('<h3 id="pro-benchmark">')
    return HTML[start:HTML.index("<h3", start + 10)]


def _script():
    return HTML[HTML.index("// Pro benchmark."):HTML.index("// Grades\n")]


# ── API passthrough ──────────────────────────────────────────────────────────
@pytest.mark.parametrize("key", list(FILES))
def test_api_passes_each_result_file_through_unchanged(benchmark, key):
    assert benchmark[key] == _committed(key)


def test_context_matches_a_recount_of_the_committed_benchmark(benchmark):
    players, ctx = _players(), benchmark["context"]
    for split in ("dev", "test"):
        rows = [r for r in players if r["split"] == split]
        got = ctx["splits"][split]
        assert got["players"] == len(rows) == _committed(split)["players_scored"]
        assert got["with_grade_words"] == sum(r["excerpt_has_grade_words"] == "True" for r in rows)
        published = [float(r[c]) for r in rows for c in r if c.startswith("pub_") and r[c].strip()]
        assert got["published_highest_any_tool"] == max(published)
        assert got["published_above_letter_cap"] == sum(v > max(scorer.LETTER_TO_2080.values()) for v in published)
        overall = [float(r["pub_overall"]) for r in rows]
        assert (got["published_overall_lowest"], got["published_overall_highest"]) == (min(overall), max(overall))
    assert ctx["sources"] == ["MLB Pipeline"]
    assert ctx["letter_scale"] == {"highest": max(scorer.LETTER_TO_2080.values()),
                                   "lowest": min(scorer.LETTER_TO_2080.values()), "step": scorer.STEP}


def test_filtered_run_is_the_test_split_minus_the_flagged_players(benchmark):
    """The page says: N of the test players are flagged, this run scores the rest."""
    test = benchmark["context"]["splits"]["test"]
    assert test["players"] - test["with_grade_words"] == _committed("test_no_grade_words")["players_scored"]
    assert _committed("test_no_grade_words")["split"] == "test"


# ── The limits the page states are true of the data ──────────────────────────
def test_letter_scale_cannot_express_75_or_80():
    values = set(scorer.LETTER_TO_2080.values())
    assert max(values) == 70 and not ({75, 80} & values)


def test_splitter_has_no_comparable_pairs_in_either_split():
    assert _committed("dev")["by_tool"]["splitter"]["n"] == 0
    assert _committed("test")["by_tool"]["splitter"]["n"] == 0


def test_overall_grades_run_high_on_every_run():
    """The limitation heading says so; fail if a new run stops supporting it."""
    for key in FILES:
        overall = _committed(key)["by_tool"]["overall"]
        assert overall["ci95"]["bias"][0] > 0, key


def test_flag_rule_in_words_is_the_rule_that_set_the_flag():
    """Local only (needs the excerpts): the rule the page describes reproduces the committed flag."""
    full = BENCH / "pro_benchmark.json"
    if not full.exists():
        pytest.skip("full benchmark file is local only")
    flags = {r["id"]: r["excerpt_has_grade_words"] for r in _players()}
    for p in json.loads(full.read_text())["players"]:
        assert str(bool(FLAG_RULE.search(p["excerpt"]))) == flags[p["id"]], p["id"]


# ── The page ─────────────────────────────────────────────────────────────────
def test_section_shows_test_first_then_filtered_then_dev():
    section = _section()
    order = [section.index(f'id="{name}"') for name in ("bench-test-table", "bench-filtered-table", "bench-dev-table")]
    assert order == sorted(order)
    assert "Test split (the headline)" in section
    assert "Development split" in section and "not the headline" in section


def test_page_reads_every_metric_and_interval_from_the_data():
    script = _script()
    for ref in ("pb.test", "pb.dev", "pb.test_no_grade_words", "'mae'", "'bias'", "'within_step'", "'spearman'",
                "'overall'", "'_pooled'", "ci95", "res.step_points", "players_scored", "T.draws"):
        assert ref in script, ref


def test_no_result_value_is_typed_into_the_section():
    """Fails if any number is written into the benchmark section by hand."""
    text = re.sub(r"<[^>]+>", " ", _section())            # drop tags, so <h3>/<h4> do not count
    assert re.findall(r"\d", text) == [], "the section's static HTML must contain no digits"
    # In the script, the only numbers allowed are formatting constants and the
    # fixed names of things: the 20-80 scale, 95% intervals, the 75 and 80 the
    # letters cannot reach, and the 20-to-85 flag rule.
    numbers = set(re.findall(r"(?<![\w.])\d+(?:\.\d+)?", _script()))
    assert numbers <= {"0", "1", "2", "5", "100", "20", "80", "85", "75", "95"}, numbers


def test_no_result_value_appears_anywhere_in_the_page():
    typed = []
    for key in FILES:
        for tool, m in _committed(key)["by_tool"].items():
            values = [m.get(k) for k in ("mae", "bias", "within_step", "spearman")]
            values += [v for pair in (m.get("ci95") or {}).values() for v in pair]
            for v in values:
                if v is None:
                    continue
                raw = f"{abs(v):.4f}"
                if len(raw.rstrip("0")) >= 6 and raw in HTML:      # 4-decimal values only; 5.0 or 0.75 could be CSS
                    typed.append((key, tool, raw))
    assert typed == []


def test_new_limitations_carry_no_typed_results():
    limits = HTML[HTML.index('id="limitations"'):]
    for title in ("Overall grades run high.", "Letter grades top out.", "One outlet's grades, short notes."):
        item = limits[limits.index(title):]
        item = re.sub(r"<[^>]+>", " ", item[:item.index("</li>")])
        assert set(re.findall(r"\d+", item)) <= {"20", "80", "75"}, title
    assert 'id="bench-limit-overall"' in limits


def test_page_states_the_limits_and_explains_the_flag():
    script = _script()
    for phrase in ("opinion, not ground truth", "cannot express a 75 or an 80", "no comparable pairs",
                   "Overall grades run high", "Each split has ${ts.players} players",
                   "partly reading the grade back", "any number from 20 to 85 that ends in 0 or 5",
                   "it also catches a velocity or a home run total"):
        assert phrase in script, phrase
