"""The /methods page and the data behind it. No API key required."""
import importlib
import json

import pytest
from fastapi.testclient import TestClient

from backend.main import app
from backend.routes import methods
from backend.services import audit

client = TestClient(app)


@pytest.fixture(autouse=True)
def temp_db(tmp_path, monkeypatch):
    """/api/methods reads the timing table; keep it off the real database file."""
    monkeypatch.setenv("FV_AUDIT_DB", str(tmp_path / "audit.db"))
    importlib.reload(audit)
    yield


def test_methods_page_is_served_and_covers_every_required_section():
    r = client.get("/methods")
    assert r.status_code == 200
    html = r.text
    for anchor in ('id="stakeholder"', 'id="sources"', 'id="matching"', 'id="evaluation"',
                   'id="benchmarks"', 'id="limitations"'):
        assert anchor in html
    for phrase in ("Coach Costanza", "Era gap", "MLB benchmarks for college players",
                   "Fictional demo players", "Small, synthetic test sets", "has not been measured"):
        assert phrase in html


def test_methods_numbers_come_from_the_committed_evaluation_files():
    d = client.get("/api/methods").json()
    committed = json.loads((methods.RESULTS / "retrieval_eval.json").read_text())
    for name, s in d["retrieval"]["systems"].items():
        assert "per_query" not in s
        assert s["role_mismatch_at_5"] == committed["systems"][name]["role_mismatch_at_5"]
    grades = json.loads((methods.RESULTS / "grade_validation.json").read_text())
    assert d["grades"] == grades


def test_sources_carry_dates():
    d = client.get("/api/methods").json()
    assert d["corpus"]["first_year"] == 1951 and d["corpus"]["last_year"] == 1964
    assert d["corpus"]["documents"] == 1919
    assert d["benchmarks"]["season"] == 2025 and d["benchmarks"]["fetched"]
    assert "2025-26" in d["basketball"]["seasons"]
    assert d["basketball"]["player_seasons"] == 5080


def test_nav_and_footer_link_to_methods():
    home = client.get("/").text
    nav = home[home.index('<nav id="navbar"'):home.index("</nav>")]
    footer = home[home.index("<footer"):home.index("</footer>")]
    assert 'href="/methods"' in nav
    assert 'href="/methods"' in footer
