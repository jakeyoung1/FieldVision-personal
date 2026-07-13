"""Basketball RAG blurb generation — display/search split and descriptor tags."""
import pandas as pd

from backend.services.rag_basketball import _descriptor_tags, _row_to_blurb

COLS = ["Player", "Pos", "Tm", "PTS", "TRB", "AST", "STL", "BLK", "3P%", "3PA", "TS%"]


def _row(values):
    return pd.Series(dict(zip(COLS, values)))


def test_name_in_display_not_in_search():
    row = _row(["Will Barton", "SG", "DEN", 14.7, 4.8, 3.9, 0.8, 0.4, 0.36, 5.0, 0.55])
    display, search = _row_to_blurb(row, COLS)
    assert display.startswith("Will Barton")
    assert "Will" not in search
    assert "Barton" not in search


def test_position_words_in_search():
    row = _row(["A", "PG", "X", 10, 3, 6, 1, 0, 0.3, 2, 0.5])
    _, search = _row_to_blurb(row, COLS)
    assert "point guard" in search
    assert "ball handler" in search


def test_scorer_and_shooter_tags():
    tags = " ".join(_descriptor_tags(
        _row(["A", "SG", "X", 25, 4, 3, 1, 0, 0.40, 6, 0.60]), COLS))
    assert "high volume scorer" in tags
    assert "three point shooter" in tags
    assert "efficient scorer" in tags


def test_no_tags_for_empty_stats():
    row = pd.Series({"Player": "B"})
    assert _descriptor_tags(row, ["Player"]) == []


def test_missing_player_returns_none():
    row = pd.Series({"PTS": 20})
    assert _row_to_blurb(row, ["PTS"]) is None
