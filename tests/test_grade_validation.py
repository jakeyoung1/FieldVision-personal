"""AI-vs-coach grade harness. No API key required.

Rows built inside these tests are test fixtures for the arithmetic only. They
are not coach grades and never touch evaluation/grades/coach_grades.csv.
"""
import csv
import json

import pytest

from evaluation import grade_validation as G


def test_letter_and_number_grades_convert_to_2080():
    assert G.to_2080("C") == 40
    assert G.to_2080("a") == 70
    assert G.to_2080("55") == 55
    assert G.to_2080("") is None
    with pytest.raises(ValueError):
        G.to_2080("95")


def test_app_recommendations_fold_onto_take_follow_hold():
    assert G.DECISION["Priority Follow"] == "Take"
    assert G.DECISION["Do Not Follow"] == "Hold"
    assert G.to_decision("follow") == "Follow"
    assert G.to_decision("") is None
    with pytest.raises(ValueError):
        G.to_decision("Maybe")


def test_spearman_and_kappa():
    assert G.spearman([1, 2, 3, 4], [10, 20, 30, 40]) == pytest.approx(1.0)
    assert G.spearman([1, 2, 3, 4], [40, 30, 20, 10]) == pytest.approx(-1.0)
    assert G.spearman([1, 2], [1, 2]) is None
    assert G.cohens_kappa(["Take", "Hold"], ["Take", "Hold"]) == pytest.approx(1.0)
    # 50% raw agreement, 50% expected by chance -> kappa 0
    assert G.cohens_kappa(["Take", "Take", "Hold", "Hold"],
                          ["Take", "Hold", "Take", "Hold"]) == pytest.approx(0.0)


def test_compare_arithmetic():
    pairs = [
        {"ai_2080": 50, "coach_2080": 45, "ai_decision": "Follow", "coach_decision": "Follow"},
        {"ai_2080": 40, "coach_2080": 50, "ai_decision": "Hold", "coach_decision": "Follow"},
        {"ai_2080": 60, "coach_2080": 55, "ai_decision": "Take", "coach_decision": "Take"},
    ]
    c = G.compare(pairs)
    assert c["mae_2080"] == pytest.approx((5 + 10 + 5) / 3, abs=0.01)
    assert c["bias_2080"] == pytest.approx((5 - 10 + 5) / 3, abs=0.01)
    assert c["decision_agreement"] == pytest.approx(2 / 3, abs=0.001)
    assert c["n_grades"] == 3


def test_placeholder_rows_are_never_scored():
    rows = [
        {"status": "PLACEHOLDER", "coach_grade": "50", "coach_decision": "Follow"},
        {"status": "coach", "coach_grade": "", "coach_decision": ""},
        {"status": "coach", "coach_grade": "45", "coach_decision": ""},
    ]
    assert G.real_coach_rows(rows) == [rows[2]]


def test_coach_sheet_is_blind_and_all_placeholder():
    with G.COACH_GRADES.open(newline="") as f:
        reader = csv.DictReader(f)
        rows = list(reader)
        assert reader.fieldnames == G.COACH_COLUMNS
    assert len(rows) == 20
    assert all(r["status"] == "PLACEHOLDER" for r in rows)
    assert all(not r["coach_grade"] and not r["coach_decision"] for r in rows)
    ai = G.load_ai()
    for r in rows:   # no AI grade leaks onto the sheet the coach reads
        assert ai[r["id"]]["grade"] not in r["notes"].split()


def test_ai_grades_cover_every_player_with_valid_values():
    ai = G.load_ai()
    assert set(ai) == {p["id"] for p in G.load_players()}
    for g in ai.values():
        assert g["grade"] in G.LETTER_TO_2080
        assert g["recommendation"] in G.DECISION


def test_retest_compares_two_ai_runs():
    a = {"x": {"grade": "B", "recommendation": "Follow"}, "y": {"grade": "C", "recommendation": "Hold"},
         "z": {"grade": "A", "recommendation": "Priority Follow"}}
    b = {"x": {"grade": "B-", "recommendation": "Follow"}, "y": {"grade": "C", "recommendation": "Hold"},
         "z": {"grade": "A", "recommendation": "Follow"}}
    t = G.retest(a, b)
    assert t["same_letter"] == pytest.approx(2 / 3, abs=0.001)
    assert t["within_one_step"] == 1.0
    assert t["decision_agreement"] == pytest.approx(2 / 3, abs=0.001)
    assert G.retest(a, {}) is None


def test_committed_retest_matches_the_two_committed_runs():
    result = json.loads((G.RESULTS / "grade_validation.json").read_text())
    fresh = G.retest(G.load_ai(), G.load_ai(G.AI_REPEAT))
    assert result["ai_retest"] == fresh
    assert set(G.load_ai(G.AI_REPEAT)) == set(G.load_ai())


def test_committed_results_report_no_comparison_until_coach_grades_exist():
    result = json.loads((G.RESULTS / "grade_validation.json").read_text())
    assert result["coach_rows_filled"] == 0
    assert result["comparison"] is None
    assert "No comparison yet" in (G.RESULTS / "grade_validation.md").read_text()
