"""Decision log and timings on both storage backends.

SQLite always runs. Postgres runs when FV_TEST_DATABASE_URL points at a
throwaway database (CI starts one); otherwise those cases are skipped.
"""
import importlib
import os

import pytest

from backend.services import audit, store, timing

PG_URL = os.environ.get("FV_TEST_DATABASE_URL", "")


@pytest.fixture(params=["sqlite", "postgres"])
def backend(request, tmp_path, monkeypatch):
    if request.param == "postgres":
        if not PG_URL:
            pytest.skip("set FV_TEST_DATABASE_URL to run against Postgres")
        monkeypatch.setenv("DATABASE_URL", PG_URL)
        monkeypatch.delenv("FV_AUDIT_DB", raising=False)
        import psycopg
        with psycopg.connect(PG_URL, autocommit=True) as conn:
            conn.execute("DROP TABLE IF EXISTS overrides, report_timings")
    else:
        monkeypatch.delenv("DATABASE_URL", raising=False)
        monkeypatch.setenv("FV_AUDIT_DB", str(tmp_path / "audit.db"))
    for module in (store, audit, timing):
        importlib.reload(module)
    yield request.param


def test_backend_selection_and_description(backend):
    assert store.backend() == backend
    d = store.describe()
    assert d["durable"] is True
    assert d["storage"] == ("postgres" if backend == "postgres" else "sqlite-file")


def test_overrides_round_trip_and_append_only(backend):
    first = audit.record("Cole Braddock", "Fastball", "B", "A-", "sat 96")
    second = audit.record("Cole Braddock", "Fastball", "B", "B+", "revised after 2nd look")
    assert second["id"] > first["id"]
    log = audit.log(player="Cole Braddock")
    assert [e["scout_grade"] for e in log] == ["B+", "A-"]       # newest first, nothing overwritten
    assert audit.summary()["total_overrides"] == 2
    assert audit.summary()["durable"] is True


def test_records_survive_a_restart(backend):
    """A restart is new processes and new connections; the rows must still be there."""
    audit.record("Danny Vasquez", "Curveball", "B", "A-", "hammer")
    timing.record("Danny Vasquez", 25, 200, "reviewed")
    for module in (store, audit, timing):
        importlib.reload(module)
    assert audit.log()[0]["player"] == "Danny Vasquez"
    assert timing.summary()["reports"] == 1


def test_timings_round_trip(backend):
    timing.record("A", 30, 120, "reviewed")
    timing.record("B", 30, 360, "exported")
    assert timing.summary()["median_minutes_to_finish"] == 4.0


def test_ephemeral_mode_is_reported_as_not_durable(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("FV_AUDIT_DB", raising=False)
    assert store.describe() == {"storage": "sqlite-ephemeral", "durable": False}


def test_unknown_table_is_refused():
    with pytest.raises(ValueError):
        store.insert("users", {"x": 1})
