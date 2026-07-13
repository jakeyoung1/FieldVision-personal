"""Pitch viz aggregation + MLB percentile math — no API key required."""
import pandas as pd
import pytest
from fastapi import HTTPException

from backend.routes.trackman_viz import build_viz
from backend.services.mlb_benchmarks import match_pitch_type, percentile


def test_percentile_at_league_mean_is_50():
    assert percentile("Fastball", "velo", 94.2) == 50


def test_percentile_hot_and_cold():
    assert percentile("Fastball", "velo", 99.0) >= 97
    assert percentile("Fastball", "velo", 89.0) <= 2


def test_percentile_unknown_type_is_none():
    assert percentile("Knuckleball", "velo", 70.0) is None


def test_match_pitch_type_aliases():
    assert match_pitch_type("FourSeamFastBall") == "FourSeamFastBall"
    assert match_pitch_type("ff") == "Fastball"
    assert match_pitch_type("curve") == "Curveball"
    assert match_pitch_type("mystery") is None


def _demo_df():
    return pd.DataFrame({
        "Pitcher": ["A", "A", "A", "B"],
        "TaggedPitchType": ["Fastball", "Fastball", "Slider", "Sinker"],
        "RelSpeed": [95.0, 96.0, 86.0, 92.0],
        "SpinRate": [2400, 2450, 2600, 2100],
        "InducedVertBreak": [17.0, 18.0, 2.0, 8.0],
        "HorzBreak": [8.0, 7.0, -6.0, 15.0],
        "PlateLocHeight": [2.5, 3.0, 1.8, 2.2],
        "PlateLocSide": [0.2, -0.3, 0.5, -0.1],
    })


def test_build_viz_structure():
    out = build_viz(_demo_df())
    assert set(out["pitchers"]) == {"A", "B"}
    a = out["pitchers"]["A"]
    assert len(a["pitches"]) == 3
    fb = next(t for t in a["types"] if t["type"] == "Fastball")
    assert fb["count"] == 2
    assert fb["avg_velo"] == 95.5
    assert fb["velo_pctile"] is not None and fb["velo_pctile"] > 60
    assert all(k in a["pitches"][0] for k in ("velo", "spin", "ivb", "hb", "loc_x", "loc_z"))


def test_build_viz_requires_pitcher_column():
    with pytest.raises(HTTPException):
        build_viz(pd.DataFrame({"RelSpeed": [90.0]}))
