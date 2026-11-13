"""Check a DATABASE_URL before putting it in Render. Read-only: writes no rows.

    DATABASE_URL='postgresql://...' .venv/bin/python scripts/check_database.py

Connects the way the app does, creates the two tables if they are missing
(the app would do the same on its first request), and prints the server
version, connection time, and how many rows each table holds.

Output is safe to share. On failure it prints the error's class and one of a
fixed set of plain messages, never the driver's own text: drivers quote the
connection string back ("missing = after postgres//user:password@host..."),
so a typo in the URL would otherwise print the password.
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.services import store  # noqa: E402


# Driver text is matched against these phrases and then discarded. Order
# matters: a malformed URL is checked first because its message echoes the URL.
_FAILURES = [
    (('missing "="', "invalid connection option", "connection info string", "invalid uri",
      "invalid port", "invalid percent", "invalid integer value"),
     "The connection string is not a valid Postgres URL."),
    (("password authentication failed", "authentication failed", "no password supplied"),
     "The server rejected the user name or password."),
    (("could not translate host name", "name or service not known", "nodename nor servname"),
     "The host in the connection string could not be found."),
    (("connection refused", "timeout expired", "timed out", "connection timeout",
      "network is unreachable", "could not connect", "could not receive data"),
     "The database server could not be reached."),
    (("does not exist",),
     "The database or user named in the connection string does not exist."),
    (("ssl",),
     "The server and client could not agree on SSL settings."),
]
_UNKNOWN = "The connection failed. The driver's message is withheld because it can contain the connection string."


def describe_failure(exc: BaseException) -> str:
    """Error class plus a fixed message. Nothing from the exception text is echoed."""
    text = str(exc).lower()
    for phrases, message in _FAILURES:
        if any(p in text for p in phrases):
            return f"{type(exc).__name__}: {message}"
    return f"{type(exc).__name__}: {_UNKNOWN}"


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
    except Exception as exc:
        print(f"Could not use this database. {describe_failure(exc)}")
        return 2
    print(f"OK: Postgres {version}, ssl={ssl}, connected and ready in {connect_ms:.0f} ms")
    print(f"Rows: overrides={counts['overrides']}, report_timings={counts['report_timings']}")
    print("The app will report storage=postgres, durable=true with this DATABASE_URL.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
