"""MLB pitch benchmarks — approximate league distributions from public Statcast data.

Used to place a pitcher's Trackman metrics on an MLB percentile scale
(Baseball Savant-style). Values are approximate league means/standard
deviations for recent MLB seasons; the UI labels them as approximations.
"""
import math

# pitch type → {metric: (mean, std)}
BENCHMARKS = {
    "Fastball":  {"velo": (94.2, 2.4), "spin": (2280, 180)},
    "FourSeamFastBall": {"velo": (94.2, 2.4), "spin": (2280, 180)},
    "Sinker":    {"velo": (93.6, 2.3), "spin": (2150, 170)},
    "TwoSeamFastBall": {"velo": (93.6, 2.3), "spin": (2150, 170)},
    "Cutter":    {"velo": (89.5, 2.5), "spin": (2400, 220)},
    "Slider":    {"velo": (85.4, 2.7), "spin": (2450, 250)},
    "Sweeper":   {"velo": (82.5, 2.6), "spin": (2550, 260)},
    "Curveball": {"velo": (79.6, 3.0), "spin": (2550, 300)},
    "Changeup":  {"velo": (85.5, 2.8), "spin": (1780, 250)},
    "ChangeUp":  {"velo": (85.5, 2.8), "spin": (1780, 250)},
    "Splitter":  {"velo": (85.0, 2.9), "spin": (1500, 260)},
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
        "sl": "Slider", "cb": "Curveball", "curve": "Curveball",
        "ch": "Changeup", "fc": "Cutter", "fs": "Splitter",
    }
    return aliases.get(raw_l)
