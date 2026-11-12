"""Storage for the decision log and report timings.

Two backends behind one small interface:

  Postgres  when DATABASE_URL is set. Survives restarts and redeploys, which a
            file on Render's free plan does not. Any Postgres works: a Render
            Postgres instance, or an external one.
  SQLite    otherwise, at FV_AUDIT_DB (default: fieldvision_audit.db in the
            repo). Durable only if that path is on a persistent disk.

Callers write SQL with "?" placeholders; they are translated for Postgres.
"""
from __future__ import annotations

import os
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path

DEFAULT_SQLITE = Path(__file__).resolve().parents[2] / "fieldvision_audit.db"

# One lock serializes writes. SQLite connections can't be shared across
# FastAPI's threadpool, and write volume here is a handful of rows a session.
_lock = threading.Lock()

_SCHEMA = {
    "overrides": """
        CREATE TABLE IF NOT EXISTS overrides (
            id           {pk},
            ts           TEXT NOT NULL,
            session_id   TEXT NOT NULL DEFAULT '',
            player       TEXT NOT NULL,
            tool         TEXT NOT NULL,
            ai_grade     TEXT,
            scout_grade  TEXT,
            reason       TEXT NOT NULL DEFAULT '',
            action       TEXT NOT NULL DEFAULT 'set'
        )""",
    "report_timings": """
        CREATE TABLE IF NOT EXISTS report_timings (
            id                {pk},
            ts                TEXT NOT NULL,
            session_id        TEXT NOT NULL DEFAULT '',
            player            TEXT NOT NULL DEFAULT '',
            seconds_to_report REAL NOT NULL,
            seconds_to_finish REAL NOT NULL,
            finished_by       TEXT NOT NULL
        )""",
}
_INDEXES = ["CREATE INDEX IF NOT EXISTS idx_overrides_player ON overrides(player)"]
_PK = {"sqlite": "INTEGER PRIMARY KEY AUTOINCREMENT", "postgres": "BIGSERIAL PRIMARY KEY"}


def database_url() -> str:
    return os.environ.get("DATABASE_URL", "").strip()


def backend() -> str:
    return "postgres" if database_url() else "sqlite"


def sqlite_path() -> Path:
    return Path(os.environ.get("FV_AUDIT_DB") or DEFAULT_SQLITE)


def describe() -> dict:
    """What the UI and /api/audit/summary report about where records live."""
    if backend() == "postgres":
        return {"storage": "postgres", "durable": True}
    if os.environ.get("FV_AUDIT_DB"):
        return {"storage": "sqlite-file", "durable": True}
    return {"storage": "sqlite-ephemeral", "durable": False}


def _sql(statement: str) -> str:
    return statement.replace("?", "%s") if backend() == "postgres" else statement


@contextmanager
def connect():
    """A connection with the schema in place; commits on success."""
    with _lock:
        if backend() == "postgres":
            import psycopg
            from psycopg.rows import dict_row
            conn = psycopg.connect(database_url(), row_factory=dict_row, connect_timeout=10)
        else:
            conn = sqlite3.connect(sqlite_path(), timeout=5)
            conn.row_factory = sqlite3.Row
        try:
            for ddl in _SCHEMA.values():
                conn.execute(ddl.format(pk=_PK[backend()]))
            for ddl in _INDEXES:
                conn.execute(ddl)
            yield conn
            conn.commit()
        finally:
            conn.close()


def insert(table: str, row: dict) -> int:
    """Insert one row; return its id."""
    if table not in _SCHEMA:
        raise ValueError(f"unknown table {table}")
    cols = list(row)
    statement = f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({', '.join('?' for _ in cols)})"
    with connect() as conn:
        if backend() == "postgres":
            cur = conn.execute(_sql(statement) + " RETURNING id", [row[c] for c in cols])
            return int(cur.fetchone()["id"])
        cur = conn.execute(statement, [row[c] for c in cols])
        return int(cur.lastrowid)


def select(statement: str, params: tuple = ()) -> list[dict]:
    with connect() as conn:
        return [dict(r) for r in conn.execute(_sql(statement), params).fetchall()]
