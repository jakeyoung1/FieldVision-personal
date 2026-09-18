"""Decision audit trail — an append-only record of every scout override.

The Evidence Chain lets a scout overrule the model. That override *is* the
accountability mechanism: it is the moment a human took responsibility for a
grade. Keeping it only in browser localStorage means the record dies with the
cache, is invisible to anyone else, and silently loses the previous value every
time a grade is revised.

This module stores those events server-side, append-only. Revising an override
writes a new row rather than mutating the old one, so the log answers "what did
this scout think, and when did they change their mind?" — not just "what do they
think now."

Storage is stdlib sqlite3, consistent with the project's zero-heavy-dependency
posture. FV_AUDIT_DB sets the file path; on ephemeral hosting (Render free tier)
the log lives for the container's lifetime, and pointing that variable at a
mounted volume makes it durable. Stated plainly rather than implied, because a
half-durable audit log is worse than one whose limits are known.
"""
from __future__ import annotations

import os
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_DB = Path(__file__).parent.parent.parent / "fieldvision_audit.db"
ACTIONS = ("set", "cleared")
GRADE_ORDER = ["F", "D", "D+", "C-", "C", "C+", "B-", "B", "B+", "A-", "A"]

# sqlite3 connections are not shareable across threads, and FastAPI serves on a
# threadpool. One lock plus a per-call connection keeps writes serialized without
# a connection pool; write volume here is a handful of rows per scouting session.
_lock = threading.Lock()


def _db_path() -> Path:
    return Path(os.environ.get("FV_AUDIT_DB") or DEFAULT_DB)


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(_db_path(), timeout=5)
    conn.row_factory = sqlite3.Row
    conn.execute("""
        CREATE TABLE IF NOT EXISTS overrides (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            ts           TEXT NOT NULL,
            session_id   TEXT NOT NULL DEFAULT '',
            player       TEXT NOT NULL,
            tool         TEXT NOT NULL,
            ai_grade     TEXT,
            scout_grade  TEXT,
            reason       TEXT NOT NULL DEFAULT '',
            action       TEXT NOT NULL DEFAULT 'set'
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_overrides_player ON overrides(player)")
    return conn


def _direction(ai_grade: str | None, scout_grade: str | None) -> str:
    """Which way the human moved the grade. Unknown grades compare as 'unknown'."""
    if ai_grade not in GRADE_ORDER or scout_grade not in GRADE_ORDER:
        return "unknown"
    delta = GRADE_ORDER.index(scout_grade) - GRADE_ORDER.index(ai_grade)
    if delta > 0:
        return "upgrade"
    if delta < 0:
        return "downgrade"
    return "unchanged"


def record(player: str, tool: str, ai_grade: str | None, scout_grade: str | None,
           reason: str = "", action: str = "set", session_id: str = "") -> dict:
    """Append one override event. Returns the stored row."""
    if action not in ACTIONS:
        action = "set"
    row = {
        "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "session_id": str(session_id)[:64],
        "player": (str(player).strip() or "Unknown")[:80],
        "tool": (str(tool).strip() or "__overall__")[:40],
        "ai_grade": str(ai_grade)[:4] if ai_grade else None,
        "scout_grade": str(scout_grade)[:4] if scout_grade else None,
        "reason": str(reason).strip()[:300],
        "action": action,
    }
    with _lock:
        conn = _connect()
        try:
            cur = conn.execute(
                "INSERT INTO overrides (ts, session_id, player, tool, ai_grade,"
                " scout_grade, reason, action) VALUES (?,?,?,?,?,?,?,?)",
                (row["ts"], row["session_id"], row["player"], row["tool"],
                 row["ai_grade"], row["scout_grade"], row["reason"], row["action"]),
            )
            conn.commit()
            row["id"] = cur.lastrowid
        finally:
            conn.close()
    row["direction"] = _direction(row["ai_grade"], row["scout_grade"])
    return row


def log(player: str | None = None, limit: int = 100) -> list[dict]:
    """Most recent override events, newest first."""
    limit = max(1, min(int(limit), 500))
    with _lock:
        conn = _connect()
        try:
            if player:
                rows = conn.execute(
                    "SELECT * FROM overrides WHERE player = ? ORDER BY id DESC LIMIT ?",
                    (str(player)[:80], limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM overrides ORDER BY id DESC LIMIT ?", (limit,)
                ).fetchall()
        finally:
            conn.close()
    out = []
    for r in rows:
        d = dict(r)
        d["direction"] = _direction(d["ai_grade"], d["scout_grade"])
        out.append(d)
    return out


def summary() -> dict:
    """Aggregate disagreement stats.

    The audit log doubles as a feedback signal: a tool the scouts keep
    downgrading is a tool the model is reading too generously.
    """
    entries = [e for e in log(limit=500) if e["action"] == "set"]
    directions = {"upgrade": 0, "downgrade": 0, "unchanged": 0, "unknown": 0}
    by_tool: dict[str, int] = {}
    for e in entries:
        directions[e["direction"]] += 1
        by_tool[e["tool"]] = by_tool.get(e["tool"], 0) + 1
    top = sorted(by_tool.items(), key=lambda kv: (-kv[1], kv[0]))[:5]
    return {
        "total_overrides": len(entries),
        "players_touched": len({e["player"] for e in entries}),
        "directions": directions,
        "most_overridden_tools": [{"tool": t, "count": c} for t, c in top],
        "durable": bool(os.environ.get("FV_AUDIT_DB")),
    }
