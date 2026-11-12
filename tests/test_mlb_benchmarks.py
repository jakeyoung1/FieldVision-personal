"""MLB benchmarks are traceable to the committed 2025 Savant files."""
from pathlib import Path

import pandas as pd
import pytest

from backend.services import mlb_benchmarks as B

DATA = Path(__file__).parent.parent / "data" / "mlb"


@pytest.fixture(scope="module")
def savant():
    speed = pd.read_csv(DATA / f"savant_pitch_arsenals_avg_speed_{B.SEASON}.csv", encoding="utf-8-sig")
    spin = pd.read_csv(DATA / f"savant_pitch_arsenals_avg_spin_{B.SEASON}.csv", encoding="utf-8-sig")
    return speed, spin


@pytest.mark.parametrize("pitch_type", list(B.SAVANT_CODES))
def test_every_benchmark_recomputes_from_the_cited_files(savant, pitch_type):
    speed, spin = savant
    code = B.SAVANT_CODES[pitch_type]
    v = speed[f"{code}_avg_speed"].dropna()
    s = spin[f"{code}_avg_spin"].dropna()
    bench = B.BENCHMARKS[pitch_type]
    assert bench["velo"] == (round(v.mean(), 1), round(v.std(), 1))
    assert bench["spin"] == (round(s.mean()), round(s.std()))
    assert bench["n"] == len(v)


def test_season_is_cited_everywhere_the_app_shows_a_benchmark():
    assert str(B.SEASON) in B.LABEL
    assert str(B.SEASON) in B.NOTE
    d = B.describe()
    assert d["season"] == B.SEASON and d["source"] and d["fetched"]


def test_trackman_spellings_share_the_benchmark():
    assert B.BENCHMARKS["FourSeamFastBall"] is B.BENCHMARKS["Fastball"]
    assert B.match_pitch_type("st") == "Sweeper"


def test_viz_response_names_the_season():
    from backend.routes.trackman_viz import build_viz
    df = pd.DataFrame({"Pitcher": ["A", "A"], "TaggedPitchType": ["Fastball", "Fastball"],
                       "RelSpeed": [95.0, 96.0], "SpinRate": [2300, 2350]})
    out = build_viz(df)
    assert out["benchmark_season"] == B.SEASON
    assert str(B.SEASON) in out["benchmark_note"] and str(B.SEASON) in out["benchmark_label"]
