"""Check a DATABASE_URL before putting it in Render. Read-only: writes no rows.

    DATABASE_URL='postgresql://...' .venv/bin/python scripts/check_database.py

Connects the way the app does, creates the two tables if they are missing
(the app would do the same on its first request), and prints the server
version, connection time, and how many rows each table holds. It never prints
the connection string, so the output is safe to share.
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.services import store  # noqa: E402


def main() -> int:
    if store.backend() != "postgres":
        print("DATABASE_URL is not set, so the app would use the local SQLite file. Nothing to check.")
        return 1
    started = time.monotonic()
    try:
        with store.connect() as conn:
            connect_ms = (time.monotonic() - started) * 1000
            version = conn.execute("SHOW server_version").fetchone()["server_version"]
            ssl = conn.execute("SHOW ssl").fetchone()["ssl"]
            counts = {t: conn.execute(f"SELECT count(*) AS n FROM {t}").fetchone()["n"]
                      for t in ("overrides", "report_timings")}
    except Exception as exc:   # report the class of failure, never the URL
        print(f"Could not use this database: {type(exc).__name__}: {str(exc).splitlines()[0][:200]}")
        return 2
    print(f"OK: Postgres {version}, ssl={ssl}, connected and ready in {connect_ms:.0f} ms")
    print(f"Rows: overrides={counts['overrides']}, report_timings={counts['report_timings']}")
    print("The app will report storage=postgres, durable=true with this DATABASE_URL.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
