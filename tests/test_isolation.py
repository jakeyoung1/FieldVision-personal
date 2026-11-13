"""The suite cannot reach a real database, whatever the environment says."""
import os
import subprocess
import sys
from pathlib import Path

import pytest

import conftest
from backend.services import audit, store

ROOT = Path(__file__).resolve().parents[1]
UNREACHABLE = "postgresql://nobody:not-a-real-password@127.0.0.1:1/none"


def test_database_url_is_removed_before_tests_run():
    assert "DATABASE_URL" not in os.environ
    assert store.backend() == "sqlite"
    assert os.environ["FV_SKIP_DOTENV"] == "1"


def test_a_stray_database_url_is_refused_before_any_socket_opens(monkeypatch, tmp_path):
    """If a test did end up with a Postgres URL, the write fails instead of landing."""
    monkeypatch.setenv("DATABASE_URL", UNREACHABLE)
    before = len(conftest._refused)
    with pytest.raises(AssertionError, match="not FV_TEST_DATABASE_URL"):
        audit.record("Nobody", "Fastball", "B", "A", "should never be stored")
    # This refusal is the point of the test, so it does not count as a leak.
    assert len(conftest._refused) == before + 1
    conftest._refused.pop()


def test_importing_the_app_does_not_read_dotenv():
    """backend.main must skip .env under test, or .env's DATABASE_URL comes back."""
    code = (
        "import dotenv\n"
        "def boom(*a, **k): raise SystemExit('load_dotenv was called')\n"
        "dotenv.load_dotenv = boom\n"
        "import backend.main\n"
        "import os; assert 'DATABASE_URL' not in os.environ\n"
    )
    env = {k: v for k, v in os.environ.items() if k != "DATABASE_URL"}
    done = subprocess.run([sys.executable, "-c", code], cwd=ROOT, env=env,
                          capture_output=True, text=True, timeout=120)
    assert done.returncode == 0, done.stderr[-400:]


def test_app_loads_dotenv_outside_tests():
    """The skip is opt-in: without FV_SKIP_DOTENV the app still reads .env."""
    source = (ROOT / "backend" / "main.py").read_text()
    assert 'if not os.environ.get("FV_SKIP_DOTENV"):' in source
    assert "load_dotenv(" in source


def test_postgres_cases_use_only_the_test_url():
    source = (ROOT / "tests" / "test_store.py").read_text()
    assert 'os.environ.get("FV_TEST_DATABASE_URL"' in source
    assert 'monkeypatch.setenv("DATABASE_URL", PG_URL)' in source
