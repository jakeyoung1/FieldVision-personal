"""Minutes per report: how long a scout spends on one FieldVision report.

Two numbers per player report, kept apart because a batch shares the first:

  seconds_to_report  the model wait: Analyze click to reports ready. Every
                     player in a batch gets the same value.
  seconds_to_finish  that player's review time: their report opened to the
                     scout marking it reviewed or exporting it.

Starting each player's clock at the batch Analyze click (as this first did)
charged the fifth report in a batch for the time spent reading the first four.

The hand-written baseline is not something the app can observe. It is
collected with the protocol on /methods#time and compared offline by
evaluation/time_study.py.

Rows live in the same store as the decision log (backend/services/store.py),
with the same durability.
"""
from __future__ import annotations

import statistics
from datetime import datetime, timezone

from backend.services import store

FINISH_EVENTS = ("reviewed", "exported")
MAX_SECONDS = 4 * 60 * 60     # a report "finished" four hours later is an abandoned tab


def record(player: str, seconds_to_report: float, seconds_to_finish: float,
           finished_by: str, session_id: str = "") -> dict:
    if finished_by not in FINISH_EVENTS:
        raise ValueError(f"finished_by must be one of {FINISH_EVENTS}")
    # The two are independent: a quick review can be shorter than the model wait.
    if not (0 < seconds_to_report <= MAX_SECONDS and 0 < seconds_to_finish <= MAX_SECONDS):
        raise ValueError("seconds_to_report and seconds_to_finish must each be above 0 and at most 4 hours")
    row = {
        "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "session_id": str(session_id)[:64],
        "player": str(player).strip()[:80],
        "seconds_to_report": round(float(seconds_to_report), 1),
        "seconds_to_finish": round(float(seconds_to_finish), 1),
        "finished_by": finished_by,
    }
    row["id"] = store.insert("report_timings", row)
    return row


def rows(limit: int = 1000) -> list[dict]:
    out = store.select("SELECT * FROM report_timings ORDER BY id DESC LIMIT ?",
                       (max(1, min(int(limit), 5000)),))
    return [dict(r) for r in out]


def summary() -> dict:
    """Median review minutes and median model wait. Nothing is reported from zero rows."""
    data = rows()
    if not data:
        return {"reports": 0, "median_minutes_to_finish": None, "median_minutes_to_report": None}
    return {
        "reports": len(data),
        "median_minutes_to_finish": round(statistics.median(r["seconds_to_finish"] for r in data) / 60, 1),
        "median_minutes_to_report": round(statistics.median(r["seconds_to_report"] for r in data) / 60, 1),
    }
