"""Scout key on override writes and player-level logs. No API key."""
import importlib

import pytest
from fastapi.testclient import TestClient

from backend.services import audit

KEY = "test-key-not-a-real-secret"
OVERRIDE = {"player": "Cole Braddock", "tool": "Fastball", "ai_grade": "B", "scout_grade": "A-", "reason": "96 late"}


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("FV_AUDIT_DB", str(tmp_path / "audit.db"))
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("FV_SCOUT_KEY", raising=False)
    monkeypatch.delenv("FV_ALLOW_OPEN_WRITES", raising=False)
    importlib.reload(audit)
    from backend.main import app
    return TestClient(app)


def test_writes_fail_closed_when_no_key_is_configured(client):
    r = client.post("/api/audit/override", json=OVERRIDE)
    assert r.status_code == 503
    assert audit.log() == []


def test_missing_or_wrong_key_is_rejected(client, monkeypatch):
    monkeypatch.setenv("FV_SCOUT_KEY", KEY)
    assert client.post("/api/audit/override", json=OVERRIDE).status_code == 401
    assert client.post("/api/audit/override", json=OVERRIDE,
                       headers={"X-Scout-Key": "wrong"}).status_code == 401
    assert audit.log() == []


def test_correct_key_writes_and_reads(client, monkeypatch):
    monkeypatch.setenv("FV_SCOUT_KEY", KEY)
    h = {"X-Scout-Key": KEY}
    assert client.post("/api/audit/override", json=OVERRIDE, headers=h).status_code == 200
    assert client.get("/api/audit/log").status_code == 401           # names need the key
    assert client.get("/api/audit/log", headers=h).json()["entries"][0]["player"] == "Cole Braddock"


def test_summary_stays_public_and_reports_configuration(client, monkeypatch):
    monkeypatch.setenv("FV_SCOUT_KEY", KEY)
    s = client.get("/api/audit/summary").json()
    assert s["scout_key_configured"] is True
    assert "entries" not in s


def test_timing_writes_and_log_need_the_key(client, monkeypatch):
    monkeypatch.setenv("FV_SCOUT_KEY", KEY)
    body = {"player": "A", "seconds_to_report": 20, "seconds_to_finish": 90, "finished_by": "reviewed"}
    assert client.post("/api/timing/report", json=body).status_code == 401
    assert client.post("/api/timing/report", json=body, headers={"X-Scout-Key": KEY}).status_code == 200
    assert client.get("/api/timing/log").status_code == 401
    assert client.get("/api/timing/summary").json()["reports"] == 1   # aggregate stays public


def test_open_dev_mode_is_explicit(client, monkeypatch):
    monkeypatch.setenv("FV_ALLOW_OPEN_WRITES", "1")
    assert client.post("/api/audit/override", json=OVERRIDE).status_code == 200


def test_no_secret_is_committed():
    from pathlib import Path
    root = Path(__file__).parent.parent
    example = (root / ".env.example").read_text()
    for name in ("FV_SCOUT_KEY", "DATABASE_URL", "FV_ALLOW_OPEN_WRITES"):
        assert name in example
    for line in example.splitlines():
        if line.startswith(("FV_SCOUT_KEY=", "DATABASE_URL=")):
            assert line.split("=", 1)[1].strip() == "", line
    render = (root / "render.yaml").read_text()
    assert "FV_SCOUT_KEY" in render and "sync: false" in render
