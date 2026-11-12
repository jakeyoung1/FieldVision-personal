"""Minutes per report: how long a finished report takes with FieldVision.

The clock starts when the scout clicks Analyze and stops when they mark the
report reviewed or export its Decision Report, so it counts the wait for the
model and the scout's review, not just API latency. One row per player report.

The hand-written baseline is not something the app can observe. It is
collected with the protocol on /methods#time and compared offline by
evaluation/time_study.py.

Rows live in the same SQLite file as the decision log (FV_AUDIT_DB), with the
same durability.
"""
from __future__ import annotations

import statistics
import threading
from datetime import datetime, timezone

from backend.services import audit

_lock = threading.Lock()
FINISH_EVENTS = ("reviewed", "exported")
MAX_SECONDS = 4 * 60 * 60     # a report "finished" four hours later is an abandoned tab


def _connect():
    conn = audit._connect()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS report_timings (
            id                INTEGER PRIMARY KEY AUTOINCREMENT,
            ts                TEXT NOT NULL,
            session_id        TEXT NOT NULL DEFAULT '',
            player            TEXT NOT NULL DEFAULT '',
            seconds_to_report REAL NOT NULL,
            seconds_to_finish REAL NOT NULL,
            finished_by       TEXT NOT NULL
        )
    """)
    return conn


def record(player: str, seconds_to_report: float, seconds_to_finish: float,
           finished_by: str, session_id: str = "") -> dict:
    if finished_by not in FINISH_EVENTS:
        raise ValueError(f"finished_by must be one of {FINISH_EVENTS}")
    if not 0 < seconds_to_report <= seconds_to_finish <= MAX_SECONDS:
        raise ValueError("timings must satisfy 0 < seconds_to_report <= seconds_to_finish <= 4h")
    row = {
        "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "session_id": str(session_id)[:64],
        "player": str(player).strip()[:80],
        "seconds_to_report": round(float(seconds_to_report), 1),
        "seconds_to_finish": round(float(seconds_to_finish), 1),
        "finished_by": finished_by,
    }
    with _lock:
        conn = _connect()
        try:
            cur = conn.execute(
                "INSERT INTO report_timings (ts, session_id, player, seconds_to_report,"
                " seconds_to_finish, finished_by) VALUES (?,?,?,?,?,?)",
                (row["ts"], row["session_id"], row["player"], row["seconds_to_report"],
                 row["seconds_to_finish"], row["finished_by"]),
            )
            conn.commit()
            row["id"] = cur.lastrowid
        finally:
            conn.close()
    return row


def rows(limit: int = 1000) -> list[dict]:
    with _lock:
        conn = _connect()
        try:
            out = conn.execute("SELECT * FROM report_timings ORDER BY id DESC LIMIT ?",
                               (max(1, min(int(limit), 5000)),)).fetchall()
        finally:
            conn.close()
    return [dict(r) for r in out]


def summary() -> dict:
    """Median minutes per finished report. Nothing is reported from zero rows."""
    data = rows()
    if not data:
        return {"reports": 0, "median_minutes_to_finish": None, "median_minutes_to_report": None}
    return {
        "reports": len(data),
        "median_minutes_to_finish": round(statistics.median(r["seconds_to_finish"] for r in data) / 60, 1),
        "median_minutes_to_report": round(statistics.median(r["seconds_to_report"] for r in data) / 60, 1),
    }
