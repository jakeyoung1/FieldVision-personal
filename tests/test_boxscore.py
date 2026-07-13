"""Box score aggregation math — no API key required."""
import pandas as pd
import pytest
from fastapi import HTTPException

from backend.routes.basketball import _summarize_players, _summary_text


def _df(rows, columns):
    return pd.DataFrame(rows, columns=columns)


COLS = ["Player", "Team", "MP", "FG", "FGA", "3P", "3PA", "FT", "FTA",
        "TRB", "AST", "STL", "BLK", "TOV", "PTS"]


def test_multi_game_averages():
    df = _df([
        ["Webb", "SMC", 34, 9, 16, 3, 7, 4, 5, 7, 3, 1, 0, 2, 25],
        ["Webb", "SMC", 31, 7, 15, 2, 6, 2, 2, 5, 4, 2, 1, 3, 18],
    ], COLS)
    s = _summarize_players(df)["Webb"]
    assert s["games"] == 2
    assert s["ppg"] == 21.5
    assert s["rpg"] == 6.0
    assert s["apg"] == 3.5
    # shooting from summed makes/attempts: 16/31
    assert s["fg_pct"] == round(16 / 31, 3)


def test_true_shooting():
    df = _df([["Okafor", "VIS", 36, 11, 19, 0, 2, 6, 9, 12, 2, 0, 3, 1, 28]], COLS)
    s = _summarize_players(df)["Okafor"]
    assert s["ts_pct"] == round(28 / (2 * (19 + 0.44 * 9)), 3)


def test_percent_fallback_when_no_attempts():
    df = _df([["Ray", "SMC", 20, 0.5]], ["Player", "Team", "MP", "FG%"])
    s = _summarize_players(df)["Ray"]
    assert s["fg_pct"] == 0.5


def test_alias_columns():
    df = _df([["Kim", 30, 8, 22]], ["Name", "MIN", "REB", "PTS"])
    s = _summarize_players(df)["Kim"]
    assert s["rpg"] == 8.0
    assert s["ppg"] == 22.0


def test_missing_player_column_raises():
    df = _df([[25, 5]], ["PTS", "AST"])
    with pytest.raises(HTTPException) as exc:
        _summarize_players(df)
    assert exc.value.status_code == 422


def test_summary_text_format():
    df = _df([["Webb", "SMC", 34, 9, 16, 3, 7, 4, 5, 7, 3, 1, 0, 2, 25]], COLS)
    text = _summary_text(_summarize_players(df))
    assert "Webb" in text
    assert "25.0 PPG" in text
    assert "TS" in text
