"""Outcome labels checked against source play-by-play. No API key required."""
from pathlib import Path

import pytest

from backend.services import play_by_play as P

FIXTURE = Path(__file__).parent.parent / "evaluation" / "capstone_2026_04"
SOURCE = (FIXTURE / "source_transcription.txt").read_text()
REPORT = (FIXTURE / "generated_report.txt").read_text()


def _events_for(last_names: set[str]) -> list[dict]:
    return [e for e in P.parse(SOURCE) if e["name"][0] in last_names]


def test_source_labels_the_5th_inning_0_1_at_bat_as_a_groundout():
    """The at-bat the capstone report called a strikeout."""
    sniffen = _events_for({"Sniffen", "Snittam"})
    by_count = {(e["inning"], e["count"]): e["kind"] for e in sniffen if e["count"]}
    assert by_count[(5, "0-1")] == "groundout"
    assert [e["kind"] for e in sniffen].count("strikeout") == 2
    assert all(e["count"] == "1-2" for e in sniffen if e["kind"] == "strikeout")


def test_ocr_variants_of_one_name_are_one_player():
    ids = {e["player"] for e in _events_for({"Sniffen", "Snittam"})}
    assert len(ids) == 1
    ids = {e["player"] for e in _events_for({"Griffith", "Griffin"})}
    assert len(ids) == 1


def test_players_with_the_same_first_name_stay_apart():
    allred = {e["player"] for e in _events_for({"Allred"})}
    venteicher = {e["player"] for e in _events_for({"Venteicher"})}
    assert allred.isdisjoint(venteicher)


def test_wild_pitch_advances_are_attributed_to_the_right_runners():
    wild = {e["name"][0] for e in P.parse(SOURCE) if e["wild_pitch"]}
    assert wild == {"Armstrong", "Karimoto"}


def test_capstone_report_flags_exactly_the_two_known_errors():
    flags = P.check_report(REPORT, SOURCE)
    assert len(flags) == 2
    k = next(f for f in flags if f["claim"] == "strikeout")
    assert "Sniffen" in k["player"] and "0-1" in k["problem"] and "groundout" in k["problem"]
    wp = next(f for f in flags if f["claim"] == "wild_pitch")
    assert "Griffith" in wp["player"]


def test_a_correct_report_raises_no_flags():
    good = ("Sniffen struck out twice, both on 1-2 counts. "
            "Griffith stole second base. Kashimoto walked.")
    assert P.check_report(good, SOURCE) == []


def test_frequency_claims_are_checked():
    flags = P.check_report("Sniffen struck out three times.", SOURCE)
    assert any("three times" in f["problem"] for f in flags)


def test_claim_about_a_count_with_no_plate_appearance_is_flagged():
    flags = P.check_report("Sniffen struck out on a 3-2 count.", SOURCE)
    assert any("no plate appearance on a 3-2" in f["problem"] for f in flags)


def test_sentences_naming_two_players_are_not_attributed():
    assert P.check_report("Sniffen and Griffith both struck out on 0-1.", SOURCE) == []


def test_quoted_evidence_and_mixed_outcomes_do_not_raise_false_flags():
    """Regression: a live Evidence Chain report quoted both at-bats verbatim and
    its markdown lines ran together, so a correct report got two flags."""
    report = (
        "## Tools\n### Hit — C- (confidence: Medium)\n"
        '- Evidence: "Sniffen, Makoa struck out swinging (1-2 BFFS)"\n'
        '- Evidence: "Snittam, Makoa grounded out to third base, 3b to 1b (0-1 K)"\n'
        "- Interpretation: Two unproductive at-bats (strikeout, groundout) for the same "
        "hitter (name spelled Sniffen/Snittam).\n"
    )
    assert P.check_report(report, SOURCE) == []


def test_a_wrong_claim_in_a_markdown_bullet_is_still_caught():
    report = "## Tools\n- Interpretation: Sniffen struck out on 0-1, chasing.\n- Next line.\n"
    flags = P.check_report(report, SOURCE)
    assert len(flags) == 1 and "groundout" in flags[0]["problem"]


@pytest.mark.parametrize("sentence", [
    "Sniffen struck out twice as SMC lost 3-2.",
    "Sniffen struck out twice in a 3-2 loss.",
    "Sniffen struck out twice; CRE won 3-2.",
    "Sniffen struck out twice and the final score was 3-2.",
    "With SMC up 2-1, Sniffen struck out twice.",
])
def test_a_game_score_is_not_read_as_a_pitch_count(sentence):
    """Regression: 'lost 3-2' was flagged as 'no plate appearance on a 3-2 count'."""
    assert P.check_report(sentence, SOURCE) == []


@pytest.mark.parametrize("sentence", [
    "Sniffen struck out on a 3-2 count.",
    "Sniffen struck out on a 3-2 pitch.",
    "Sniffen struck out on 3-2.",
    "The count went to 3-2 and Sniffen struck out.",
])
def test_a_real_count_is_still_checked(sentence):
    flags = P.check_report(sentence, SOURCE)
    assert any("no plate appearance on a 3-2" in f["problem"] for f in flags)


def test_counts_and_scores_are_told_apart():
    assert P._counts_in("struck out on a 1-2 pitch in a 3-2 win") == ["1-2"]
    assert P._counts_in("led off with a 3-1 single") == ["3-1"]      # "led off" is not a lead
    assert P._counts_in("SMC leads 3-2") == []
    assert P._counts_in("(0-1 K)") == ["0-1"]


@pytest.mark.parametrize("text,count", [
    ("Popped up 2-2", "2-2"),
    ("down 0-2", "0-2"),
    ("ahead 2-0", "2-0"),
    ("fell behind 0-2", "0-2"),
    ("He got ahead 0-2 and struck him out", "0-2"),
    ("lost the zone and fell behind 2-0", "2-0"),
    ("they were down 0-2 in the count", "0-2"),
    ("The RHP was down 2-0 to the leadoff man", "2-0"),
])
def test_count_idioms_are_kept(text, count):
    """Regression (PR #9 review): bare up/down/ahead/behind/fell were treated as
    score words, so these real counts were dropped."""
    assert P._counts_in(text) == [count]


@pytest.mark.parametrize("text", [
    "With SMC up 2-1",
    "CRE went up 3-2 in the 5th",
    "they were down 3-0 early",
    "SMC fell behind 2-0 in the first",
    "SMC fell 3-2",
    "fell to CRE 3-2",
])
def test_team_margins_and_results_are_still_scores(text):
    assert P._counts_in(text) == []


def test_a_dropped_count_no_longer_hides_a_wrong_claim():
    """With the bug, 'fell behind 0-1' gave no count, so this claim went unchecked."""
    flags = P.check_report("Sniffen fell behind 0-1 and struck out.", SOURCE)
    assert len(flags) == 1 and "groundout" in flags[0]["problem"]


def test_a_score_in_the_source_does_not_become_an_at_bats_count():
    events = P.parse("Bottom of 9th\nSmith, John singled to left; SMC leads 3-2\nJones, Al walked (3-1 BBBKB)")
    assert [(e["name"][0], e["count"]) for e in events] == [("Smith", None), ("Jones", "3-1")]


@pytest.mark.parametrize("sentence", [
    "Griffith scored on a walk-off single.",
    "Griffith came up for the walk off.",
    "Griffith has the best walk-up music on the team.",
    "Griffith walks up to the plate with a plan.",
])
def test_walk_off_and_walk_up_are_not_walks(sentence):
    """Griffith never walked in this game, so a real walk claim would be flagged."""
    assert P.check_report(sentence, SOURCE) == []


def test_a_real_walk_claim_is_still_checked():
    flags = P.check_report("Griffith walked in the 4th.", SOURCE)
    assert [f["claim"] for f in flags] == ["walk"]
    assert P.check_report("Kashimoto walked with the bases loaded.", SOURCE) == []


def test_notes_without_play_by_play_are_left_alone():
    notes = "Cole Braddock\nRHP. Fastball sits 94-96. Slider is the out pitch."
    assert P.parse(notes) == []
    assert P.check_report("Braddock struck out the side.", notes) == []
