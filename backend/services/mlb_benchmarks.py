"""MLB pitch benchmarks, 2025 regular season, from Baseball Savant.

Places a pitcher's Trackman averages on an MLB scale: "this fastball's average
velocity would rank at about the 70th percentile of 2025 MLB pitchers."

Source: Baseball Savant pitch-arsenals leaderboard, 2025, pitchers with at
least 100 pitches, "avg_speed" and "avg_spin" views. The two CSVs are committed
in data/mlb/ exactly as downloaded on FETCHED. Each benchmark is the mean and
standard deviation of per-pitcher averages for that pitch type (pitchers, not
pitches, are the unit, matching how the app compares one pitcher's average).
tests/test_mlb_benchmarks.py recomputes every number below from those files.

The percentile assumes a normal distribution over those pitcher averages. It
is an approximation, and the UI says so. These are MLB pitchers; FieldVision's
users are college pitchers, so a college arm will usually sit low on this
scale. That is the point of the comparison, not a flaw in it.
"""
import math

SEASON = 2025
SOURCE = "Baseball Savant pitch-arsenals leaderboard (min. 100 pitches)"
FETCHED = "2026-11-12"
LABEL = f"{SEASON} MLB"
NOTE = (f"Percentiles vs {SEASON} MLB pitchers (Baseball Savant, per-pitcher averages, "
        "min. 100 pitches). Approximate: assumes a normal distribution.")

# Savant pitch-type code for each benchmark, used by the recomputation test.
SAVANT_CODES = {"Fastball": "ff", "Sinker": "si", "Cutter": "fc", "Slider": "sl",
                "Sweeper": "st", "Curveball": "cu", "Changeup": "ch", "Splitter": "fs"}

# pitch type -> {metric: (mean, std)}, and how many pitchers each rests on
_BASE = {
    "Fastball":  {"velo": (94.3, 2.4), "spin": (2308, 143), "n": 679},
    "Sinker":    {"velo": (93.7, 2.5), "spin": (2215, 142), "n": 528},
    "Cutter":    {"velo": (89.5, 2.6), "spin": (2385, 179), "n": 327},
    "Slider":    {"velo": (86.0, 2.6), "spin": (2429, 210), "n": 514},
    "Sweeper":   {"velo": (82.1, 2.8), "spin": (2583, 229), "n": 332},
    "Curveball": {"velo": (80.0, 3.8), "spin": (2546, 273), "n": 371},
    "Changeup":  {"velo": (86.4, 3.3), "spin": (1773, 285), "n": 490},
    "Splitter":  {"velo": (86.4, 3.0), "spin": (1315, 324), "n": 146},
}

# Trackman spells some pitch types differently; they share a benchmark.
BENCHMARKS = {
    **_BASE,
    "FourSeamFastBall": _BASE["Fastball"],
    "TwoSeamFastBall": _BASE["Sinker"],
    "ChangeUp": _BASE["Changeup"],
}


def _norm_cdf(x: float, mean: float, std: float) -> float:
    return 0.5 * (1 + math.erf((x - mean) / (std * math.sqrt(2))))


def percentile(pitch_type: str, metric: str, value: float) -> int | None:
    """MLB percentile (0-100) for a pitch metric, or None if no benchmark."""
    bench = BENCHMARKS.get(pitch_type, {}).get(metric)
    if bench is None or value is None:
        return None
    return round(100 * _norm_cdf(value, *bench))


def match_pitch_type(raw: str) -> str | None:
    """Map a Trackman pitch-type label onto a benchmark key."""
    if not raw:
        return None
    raw_l = str(raw).strip().lower()
    for key in BENCHMARKS:
        if key.lower() == raw_l:
            return key
    aliases = {
        "four-seam": "Fastball", "fourseam": "Fastball", "ff": "Fastball",
        "two-seam": "Sinker", "twoseam": "Sinker", "si": "Sinker",
        "sl": "Slider", "st": "Sweeper", "cb": "Curveball", "cu": "Curveball",
        "curve": "Curveball", "ch": "Changeup", "fc": "Cutter", "fs": "Splitter",
    }
    return aliases.get(raw_l)


def describe() -> dict:
    """Everything the UI and /methods need to cite these numbers."""
    return {
        "season": SEASON, "source": SOURCE, "fetched": FETCHED, "label": LABEL, "note": NOTE,
        "pitch_types": {k: {"velo_mean": v["velo"][0], "velo_sd": v["velo"][1],
                            "spin_mean": v["spin"][0], "spin_sd": v["spin"][1], "pitchers": v["n"]}
                        for k, v in _BASE.items()},
    }
