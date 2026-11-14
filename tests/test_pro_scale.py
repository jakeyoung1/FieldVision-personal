"""The overall on the published pro scale: an added calibration, with a frozen rule.

No API key required: the model call is replaced with a fixed evaluation.
"""
import csv
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.main import app
from backend.services import play_by_play, pro_scale, rag, scout_report
from evaluation import grade_validation
from evaluation.pro_benchmark import score_pro_benchmark as scorer

ROOT = Path(__file__).parent.parent
BENCH = ROOT / "evaluation" / "pro_benchmark"
HTML = (ROOT / "index.html").read_text()
client = TestClient(app)

NOTES = ("Cole Braddock\nRHP, junior, 6'3 205. Fastball sits 94-96, touched 97 twice this spring. "
         "Slider is the out pitch, mid-80s with late bite. Changeup stays firm.")
HITTER_NOTES = "Marcus Hale\nSS, sophomore. Line-drive swing, uses the whole field. Runs well, sure hands at shortstop."


def _data():
    return {
        "name": "Cole Braddock", "position": "RHP", "grade": "B+", "summary": "Power arm with two pitches.",
        "tools": [
            {"name": "Fastball", "grade": "A-", "evidence": ["Fastball sits 94-96, touched 97 twice this spring"],
             "measurement": None, "interpretation": "carries the profile", "projection": "may add a tick", "conflict": None},
            {"name": "Slider", "grade": "B", "evidence": ["Slider is the out pitch, mid-80s with late bite", "not in the notes"],
             "measurement": None, "interpretation": "misses bats", "projection": "", "conflict": None},
            {"name": "Changeup", "grade": "C", "evidence": ["Changeup stays firm"],
             "measurement": None, "interpretation": "third pitch", "projection": "needs separation", "conflict": None}],
        "strengths": ["velocity"], "concerns": ["changeup"], "risk": "third pitch", "recommendation": "Follow",
        "recommendation_reason": "two real pitches", "what_would_change": ["a usable changeup"],
    }


# ── The rule: both roles, every letter, both ends of the scale ───────────────
def test_pitchers_step_down_two_and_hitters_one():
    expected = {  # letter: (pitcher, position player)
        "A": (60, 65), "A-": (55, 60), "B+": (50, 55), "B": (45, 50), "B-": (40, 45), "C+": (35, 40),
        "C": (30, 35), "C-": (25, 30), "D+": (20, 25), "D": (20, 20), "F": (20, 20)}
    assert set(expected) == set(scout_report.GRADE_SCALE)
    for letter, (pitcher, hitter) in expected.items():
        assert pro_scale.calibrate(letter, NOTES)["value"] == pitcher, letter
        assert pro_scale.calibrate(letter, HITTER_NOTES)["value"] == hitter, letter


def test_result_stays_on_the_scale_at_both_ends():
    assert pro_scale.step_down(20, rag.ROLE_PITCHER) == 20        # floor, not 10
    assert pro_scale.step_down(25, rag.ROLE_PITCHER) == 20        # floor, not 15
    assert pro_scale.step_down(30, rag.ROLE_PITCHER) == 20        # lands exactly on the floor
    assert pro_scale.step_down(20, rag.ROLE_HITTER) == 20         # floor, not 15
    assert pro_scale.step_down(25, rag.ROLE_HITTER) == 20
    assert pro_scale.step_down(80, rag.ROLE_PITCHER) == 70 and pro_scale.step_down(80, rag.ROLE_HITTER) == 75
    assert pro_scale.clamp(95) == 80 and pro_scale.clamp(5) == 20 and pro_scale.clamp(45) == 45
    for letter in scout_report.GRADE_SCALE:
        for notes in (NOTES, HITTER_NOTES):
            value = pro_scale.calibrate(letter, notes)["value"]
            assert 20 <= value <= 80 and value % 5 == 0


def test_no_clear_role_means_no_value():
    assert pro_scale.step_down(55, rag.ROLE_MIXED) is None and pro_scale.step_down(55, rag.ROLE_UNKNOWN) is None
    field = pro_scale.calibrate("B", "Good athlete. Plays hard.", None)
    assert field["value"] is None and field["role"] is None and field["steps_down"] is None
    assert field["basis"] == pro_scale.NO_ROLE and field["from_grade"] == "B"
    two_way = pro_scale.calibrate("B", "Good athlete. Plays hard.", "RHP/OF")     # both roles named: no rule
    assert two_way["value"] is None


def test_role_comes_from_the_notes_then_the_position_line():
    assert pro_scale.role_for(NOTES, "SS") == (rag.ROLE_PITCHER, "notes")         # the notes win
    assert pro_scale.role_for(HITTER_NOTES, None) == (rag.ROLE_HITTER, "notes")
    assert pro_scale.role_for("Good athlete. Plays hard.", "RHP") == (rag.ROLE_PITCHER, "position")
    assert pro_scale.role_for("Good athlete. Plays hard.", "SS") == (rag.ROLE_HITTER, "position")
    assert pro_scale.role_for("", "") == (None, None)


# ── Frozen: the rule and the numbers it was validated with ───────────────────
def test_rule_is_frozen():
    """The benchmark's test split was used once to validate exactly this rule.
    Changing a step size, the step, or the table would be tuning on that split."""
    assert pro_scale.STEPS_DOWN == {"pitcher": 2, "hitter": 1}
    assert pro_scale.STEP == scorer.STEP == 5
    assert (pro_scale.SCALE_MIN, pro_scale.SCALE_MAX) == (20, 80)
    assert pro_scale.LETTER_TO_2080 == scorer.LETTER_TO_2080 == grade_validation.LETTER_TO_2080


@pytest.mark.parametrize("split, mae, bias, within", [("dev", 5.0, 0.2083, 0.75), ("test", 5.0, 1.6667, 0.7292)])
def test_rule_reproduces_the_validated_result(split, mae, bias, within):
    """The shipped function, applied to the committed grades, gives the numbers reported on the relay."""
    with (BENCH / "pro_benchmark.public.csv").open(newline="", encoding="utf-8-sig") as f:
        rows = {r["id"]: r for r in csv.DictReader(f) if r["split"] == split}
    grades = json.loads((BENCH / f"fieldvision_grades_{split}.json").read_text())["grades"]
    assert {g["id"] for g in grades} == set(rows)
    pairs = [(pro_scale.step_down(pro_scale.LETTER_TO_2080[g["grade"]], rows[g["id"]]["role"]),
              float(rows[g["id"]]["pub_overall"])) for g in grades]
    m = scorer.metrics(pairs)
    assert (round(m["mae"], 4), round(m["bias"], 4), round(m["within_step"], 4)) == (mae, bias, within)


def test_app_role_detection_matches_the_benchmark_roles_on_the_local_notes():
    """Validation used the benchmark's role column; the app reads the role from the notes.
    Runs where the full benchmark file exists (never in CI)."""
    full = BENCH / "pro_benchmark.json"
    if not full.exists():
        pytest.skip("full benchmark file is local only")
    players = json.loads(full.read_text())["players"]
    wrong = [p["id"] for p in players if pro_scale.role_for(p["note_for_app"])[0] != p["role"]]
    assert wrong == []


# ── Additive: the letter, the tools, and the old report text are unchanged ───
# What finalize() returned for _data() on main before this field existed.
BEFORE = {
    "name": "Cole Braddock", "position": "RHP", "grade": "B+", "summary": "Power arm with two pitches.",
    "tools": [
        {"name": "Fastball", "grade": "A-", "evidence": ["Fastball sits 94-96, touched 97 twice this spring"],
         "evidence_verified": True, "measurement": None, "interpretation": "carries the profile",
         "projection": "may add a tick", "conflict": None, "confidence": "Low"},
        {"name": "Slider", "grade": "B", "evidence": ["Slider is the out pitch, mid-80s with late bite"],
         "evidence_verified": True, "measurement": None, "interpretation": "misses bats",
         "projection": "", "conflict": None, "confidence": "Low"},
        {"name": "Changeup", "grade": "C", "evidence": ["Changeup stays firm"],
         "evidence_verified": True, "measurement": None, "interpretation": "third pitch",
         "projection": "needs separation", "conflict": None, "confidence": "Low"}],
    "strengths": ["velocity"], "concerns": ["changeup"], "risk": "third pitch", "recommendation": "Follow",
    "recommendation_reason": "two real pitches", "what_would_change": ["a usable changeup"],
    "confidence": "Low", "had_measurement": False,
}
MARKDOWN_BEFORE = (
    '## Player Overview\nPower arm with two pitches.\n\n**Recommendation: Follow** — two real pitches\n'
    '**Overall confidence:** Low\n\n## Tools\n### Fastball — A- (confidence: Low)\n'
    '- Evidence: "Fastball sits 94-96, touched 97 twice this spring"\n- Interpretation: carries the profile\n'
    '- Projection: may add a tick\n\n### Slider — B (confidence: Low)\n'
    '- Evidence: "Slider is the out pitch, mid-80s with late bite"\n- Interpretation: misses bats\n\n'
    '### Changeup — C (confidence: Low)\n- Evidence: "Changeup stays firm"\n- Interpretation: third pitch\n'
    '- Projection: needs separation\n\n## Key Strengths\n- velocity\n\n## Areas of Concern\n- changeup\n\n'
    '## Risk\nthird pitch\n\n## What Would Change This Evaluation\n- a usable changeup\n\n'
    '## Recommendation & Grade\nB+ — Follow')


def test_finalize_adds_one_field_and_changes_nothing_else():
    out = scout_report.finalize(_data(), NOTES, had_measurement=False)
    assert set(out) - set(BEFORE) == {"pro_scale"}
    assert {k: out[k] for k in BEFORE} == BEFORE                  # letter, tools, everything else: identical
    assert list(out["tools"][0]) == list(BEFORE["tools"][0])      # no new key on a tool
    field = out["pro_scale"]
    assert field == {
        "label": "Published pro scale", "kind": "calibration", "value": 50, "scale": "20 to 80",
        "from_grade": "B+", "from_value": 60, "role": "pitcher", "role_source": "notes", "steps_down": 2,
        "basis": pro_scale.BASIS}
    assert "calibration" in field["basis"] and "not a second evaluation" in field["basis"]


def test_an_invalid_letter_is_defaulted_before_calibration_as_before():
    out = scout_report.finalize({"grade": "S+", "tools": []}, NOTES, had_measurement=False)
    assert out["grade"] == "C" and out["pro_scale"]["from_grade"] == "C" and out["pro_scale"]["value"] == 30


def test_text_report_keeps_every_old_line_and_adds_one():
    out = scout_report.finalize(_data(), NOTES, had_measurement=False)
    md = scout_report.to_markdown(out)
    assert md == MARKDOWN_BEFORE + "\nPublished pro scale (calibration of the overall letter B+): 50 on the 20 to 80 scale"
    without = {k: v for k, v in out.items() if k != "pro_scale"}                  # a report saved before the field
    assert scout_report.to_markdown(without) == MARKDOWN_BEFORE
    no_role = scout_report.finalize({**_data(), "position": None}, "Good athlete. Plays hard.", had_measurement=False)
    assert "pro scale" not in scout_report.to_markdown(no_role).lower()


def test_added_line_raises_no_play_by_play_flag():
    out = scout_report.finalize(_data(), NOTES, had_measurement=False)
    source = "Braddock struck out Hale on a 3-2 count in the 4th.\nBraddock walked Diaz in the 5th."
    assert play_by_play.check_report(scout_report.to_markdown(out), source) == play_by_play.check_report(MARKDOWN_BEFORE, source)
    assert play_by_play.check_report(pro_scale.markdown_line(out["pro_scale"]), source) == []


def test_analyze_returns_the_field_beside_the_unchanged_letter(monkeypatch):
    monkeypatch.setattr(scout_report, "evaluate",
                        lambda notes, rag_context="", trackman_context="": scout_report.finalize(_data(), notes, False))
    r = client.post("/api/analyze", files={"files_upload": ("braddock.txt", NOTES.encode(), "text/plain")})
    assert r.status_code == 200
    result = r.json()["results"][0]
    assert result["structured"]["grade"] == "B+" and result["profile"]["grade"] == "B+"
    assert [t["grade"] for t in result["structured"]["tools"]] == ["A-", "B", "C"]
    assert result["structured"]["pro_scale"]["value"] == 50
    assert result["report"].endswith("50 on the 20 to 80 scale")


# ── The page: beside the letter, labeled, and nothing typed ──────────────────
def test_page_shows_it_beside_the_overall_in_all_three_places():
    pill = HTML[HTML.index("function proScalePill"):HTML.index("function overrideBadge")]
    assert "c.value == null" in pill and "escAudit(c.label)" in pill and "escAudit(c.kind)" in pill
    assert 'title="${escAudit(c.basis)}"' in pill
    closed = HTML[HTML.index("function closedCard"):HTML.index("const REC_STYLE")]
    opened = HTML[HTML.index("function structuredCard"):HTML.index("function decisionReportHtml")]
    printed = HTML[HTML.index("function decisionReportHtml"):]
    for block, letter in ((closed, "${escAudit(grade)}</span>"), (opened, "${s.grade}</span>")):
        assert block.index(letter) < block.index("${proScalePill(s)}")           # right after the letter badge
        assert block.count("${proScalePill(s)}") == 1
    assert '<span class="fv-grade-badge ${gradeClass(s.grade)}">${s.grade}</span>' in opened   # letter badge untouched
    assert "s.pro_scale.value != null" in printed and "escAudit(s.pro_scale.basis)" in printed
    assert printed.index("${(overall ? overall.grade : s.grade)} · ${s.recommendation}") < printed.index("fv-pro-scale-line")


def test_page_types_no_rule_text_or_step_sizes():
    assert pro_scale.LABEL not in HTML and "lowered two steps" not in HTML
    assert "STEPS_DOWN" not in HTML and "steps_down" not in HTML
