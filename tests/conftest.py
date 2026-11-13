"""Keep the test suite away from every real database.

The app reads DATABASE_URL, and a developer's .env holds the production one.
Before this file existed, importing backend.main loaded .env, DATABASE_URL
won over the temp SQLite path the fixtures set, and tests wrote to production.

Rules enforced here:
  1. DATABASE_URL from the environment or .env is never used by a test.
  2. Postgres tests run only against FV_TEST_DATABASE_URL, a throwaway
     database (tests drop its tables).
  3. Any other attempt to open a Postgres connection fails the test before a
     socket is opened, and is counted in the run summary.
"""
import os
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

# This runs before any test module is imported, so before backend.main can
# load .env. The app URL is kept only to refuse a test run pointed at it.
_APP_URLS = {os.environ.pop("DATABASE_URL", "").strip()}
os.environ["FV_SKIP_DOTENV"] = "1"
try:
    from dotenv import dotenv_values
    _APP_URLS.add((dotenv_values(ROOT / ".env").get("DATABASE_URL") or "").strip())
except ImportError:
    pass
_APP_URLS.discard("")

TEST_DATABASE_URL = os.environ.get("FV_TEST_DATABASE_URL", "").strip()
_refused: list[str] = []


def pytest_configure(config):
    if TEST_DATABASE_URL and TEST_DATABASE_URL in _APP_URLS:
        pytest.exit("FV_TEST_DATABASE_URL is the app's own DATABASE_URL. Tests drop tables; "
                    "point it at a throwaway database.", returncode=2)


@pytest.fixture(autouse=True)
def _no_real_database(monkeypatch):
    """Each test starts with no DATABASE_URL, and psycopg can only reach the test database."""
    monkeypatch.delenv("DATABASE_URL", raising=False)
    try:
        import psycopg
    except ImportError:
        yield
        return
    real_connect = psycopg.connect

    def guarded_connect(conninfo="", *args, **kwargs):
        if not TEST_DATABASE_URL or conninfo != TEST_DATABASE_URL:
            _refused.append(os.environ.get("PYTEST_CURRENT_TEST", "?"))
            raise AssertionError("A test tried to open a Postgres connection that is not "
                                 "FV_TEST_DATABASE_URL. Refused before connecting.")
        return real_connect(conninfo, *args, **kwargs)

    monkeypatch.setattr(psycopg, "connect", guarded_connect)
    yield


def pytest_terminal_summary(terminalreporter):
    target = "FV_TEST_DATABASE_URL" if TEST_DATABASE_URL else "none (no FV_TEST_DATABASE_URL)"
    terminalreporter.write_line(
        f"database isolation: Postgres target = {target}; "
        f"refused connection attempts = {len(_refused)}")
    for test in _refused:
        terminalreporter.write_line(f"  refused in: {test}")
