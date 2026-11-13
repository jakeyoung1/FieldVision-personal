"""Minutes-per-report logging, the time study, the pilot plan, and baseball focus.

Timing rows created here are test fixtures in a throwaway database. They are
not measurements and never reach evaluation/time_study/.
"""
import csv
import importlib
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.services import audit, timing
from evaluation import time_study as T

ROOT = Path(__file__).parent.parent


@pytest.fixture(autouse=True)
def temp_db(tmp_path, monkeypatch):
    monkeypatch.setenv("FV_AUDIT_DB", str(tmp_path / "audit.db"))
    importlib.reload(audit)
    yield


@pytest.fixture
def client():
    from backend.main import app
    return TestClient(app)


def test_timing_rejects_impossible_values():
    with pytest.raises(ValueError):
        timing.record("A", 0, 20, "reviewed")           # no model wait at all
    with pytest.raises(ValueError):
        timing.record("A", 30, -5, "reviewed")          # negative review time
    with pytest.raises(ValueError):
        timing.record("A", 30, 60, "walked away")       # unknown finish event
    with pytest.raises(ValueError):
        timing.record("A", 30, 5 * 3600, "exported")    # abandoned tab


def test_review_time_may_be_shorter_than_the_model_wait():
    """The clock starts when the report is opened, so a quick review beats a slow model."""
    row = timing.record("A", 45, 20, "reviewed")
    assert (row["seconds_to_report"], row["seconds_to_finish"]) == (45, 20)


def test_summary_reports_nothing_from_no_rows_then_medians():
    assert timing.summary()["median_minutes_to_finish"] is None
    timing.record("A", 30, 120, "reviewed")
    timing.record("B", 30, 360, "exported")
    timing.record("C", 30, 240, "reviewed")
    s = timing.summary()
    assert s["reports"] == 3
    assert s["median_minutes_to_finish"] == 4.0
    assert s["median_minutes_to_report"] == 0.5


def test_timing_api_round_trip(client, monkeypatch):
    monkeypatch.setenv("FV_SCOUT_KEY", "test-key")
    h = {"X-Scout-Key": "test-key"}
    r = client.post("/api/timing/report", headers=h, json={"player": "Cole Braddock", "seconds_to_report": 25,
                                                           "seconds_to_finish": 190, "finished_by": "reviewed"})
    assert r.status_code == 200
    assert client.post("/api/timing/report", headers=h, json={"seconds_to_report": 9, "seconds_to_finish": 0,
                                                              "finished_by": "reviewed"}).status_code == 422
    assert client.get("/api/timing/log", headers=h).json()["rows"][0]["player"] == "Cole Braddock"


def test_time_study_never_counts_placeholder_rows():
    rows = [{"status": "PLACEHOLDER", "minutes": "12"}, {"status": "measured", "minutes": "14"},
            {"status": "", "minutes": "9"}]
    assert T.hand_minutes(rows) == [14.0]
    with pytest.raises(ValueError):
        T.hand_minutes([{"status": "measured", "minutes": "0"}])


def test_app_minutes_charge_review_time_plus_the_whole_model_wait():
    rows = [{"seconds_to_report": "30", "seconds_to_finish": "150"},     # 2.5 review + 0.5 wait
            {"seconds_to_report": "30", "seconds_to_finish": "90"},      # same batch, same wait
            {"seconds_to_report": "", "seconds_to_finish": ""}]          # header-only export
    assert T.app_minutes(rows) == [3.0, 2.0]


def test_time_study_comparison_math():
    c = T.compare([10, 14, 12], [4.0, 6.0])
    assert c["by_hand"]["median_minutes"] == 12
    assert c["fieldvision"]["median_minutes"] == 5.0
    assert c["fieldvision_over_hand"] == pytest.approx(0.42, abs=0.01)
    assert T.compare([], [5.0])["fieldvision_over_hand"] is None


def test_committed_time_study_is_empty_and_says_so():
    with T.HAND.open(newline="") as f:
        hand = list(csv.DictReader(f))
    assert len(hand) == 10 and all(r["status"] == "PLACEHOLDER" and not r["minutes"] for r in hand)
    assert "No measurement yet" in (T.RESULTS / "time_study.md").read_text()


def test_methods_documents_the_protocol_and_a_planned_pilot(client):
    html = client.get("/methods").text
    assert 'id="time"' in html and 'id="pilot"' in html
    assert "Planned. Not started." in html and "No pilot data exists" in html
    for criterion in ("half the hand-written median", "70% or higher", "7.5 points or less", "90% of quotes"):
        assert criterion in html
    assert client.get("/api/methods").json()["app_timings"]["reports"] == 0


def test_baseball_is_the_focus_and_the_footer_names_both_sports():
    html = (ROOT / "index.html").read_text()
    assert "<title>FieldVision · Baseball Scouting Intelligence</title>" in html
    footer = html[html.index("<footer"):html.index("</footer>")]
    assert "baseball" in footer.lower() and "basketball" in footer.lower()
    assert 'id="fv-bb-note"' in html and "Generalization test." in html
    assert "Multi-Sport Scouting Intelligence" not in html


def test_page_uses_the_per_player_clock():
    """Behavior is tested in tests/js/report_timing.test.js; this checks the wiring."""
    html = (ROOT / "index.html").read_text()
    assert '<script src="static/js/report_timing.js"></script>' in html
    assert (ROOT / "static" / "js" / "report_timing.js").exists()
    # A batch report's clock starts in fvOpenReport, not when results arrive.
    open_fn = html[html.index("window.fvOpenReport"):html.index("function closedCard")]
    assert "FVTiming.open(r, performance.now())" in open_fn
    assert "if (results.length === 1) { r._open = true; FVTiming.open(r, readyAt); }" in html
    assert "finishReport(ri, 'reviewed')" in html and "finishReport(+ri, 'exported')" in html
    # The reviewed marker is drawn only from the saved state; failure offers a retry.
    button = html[html.index("function reviewedButton"):html.index("window.fvMarkReviewed")]
    assert button.index("t.state === 'saved'") < button.index("data-reviewed")
    assert "data-timing-error" in button and "fvRetryTiming" in button
    assert "_finished" not in html
