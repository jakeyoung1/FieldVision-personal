"""Decision audit trail — append-only override log and disagreement stats. No API key."""
import importlib

import pytest

from backend.services import audit


@pytest.fixture(autouse=True)
def temp_db(tmp_path, monkeypatch):
    """Point every test at its own database file."""
    monkeypatch.setenv("FV_AUDIT_DB", str(tmp_path / "audit.db"))
    importlib.reload(audit)
    yield


def test_record_returns_stored_row():
    row = audit.record("Chris Rodriguez", "Fastball", "B", "A-", "sat 96 in the 7th")
    assert row["player"] == "Chris Rodriguez"
    assert row["tool"] == "Fastball"
    assert row["scout_grade"] == "A-"
    assert row["reason"] == "sat 96 in the 7th"
    assert row["action"] == "set"
    assert row["ts"].endswith("+00:00")
    assert row["id"] >= 1


def test_log_is_append_only_across_revisions():
    """Revising an override must not overwrite the earlier judgment."""
    audit.record("Chris Rodriguez", "Slider", "B+", "A-", "plus pitch")
    audit.record("Chris Rodriguez", "Slider", "B+", "B", "flattened out in the 5th")
    entries = audit.log(player="Chris Rodriguez")
    assert len(entries) == 2
    assert [e["scout_grade"] for e in entries] == ["B", "A-"]   # newest first


def test_cleared_action_is_recorded_not_deleted():
    audit.record("Sam Diaz", "Power", "C+", "B", "raw pop plays in games")
    audit.record("Sam Diaz", "Power", "C+", None, "", action="cleared")
    entries = audit.log(player="Sam Diaz")
    assert [e["action"] for e in entries] == ["cleared", "set"]


def test_direction_classifies_grade_movement():
    up = audit.record("A", "Arm", "B", "A")
    down = audit.record("B", "Arm", "A", "C+")
    same = audit.record("C", "Arm", "B", "B")
    unknown = audit.record("D", "Arm", None, "B")
    assert up["direction"] == "upgrade"
    assert down["direction"] == "downgrade"
    assert same["direction"] == "unchanged"
    assert unknown["direction"] == "unknown"


def test_log_filters_by_player():
    audit.record("Player One", "Speed", "B", "B+")
    audit.record("Player Two", "Speed", "B", "C")
    assert len(audit.log(player="Player One")) == 1
    assert len(audit.log()) == 2


def test_summary_counts_disagreement():
    audit.record("P1", "Fastball", "B", "A")       # upgrade
    audit.record("P2", "Fastball", "A", "B")       # downgrade
    audit.record("P3", "Command", "C", "C")        # unchanged
    audit.record("P3", "Command", "C", None, action="cleared")   # excluded
    s = audit.summary()
    assert s["total_overrides"] == 3
    assert s["players_touched"] == 3
    assert s["directions"]["upgrade"] == 1
    assert s["directions"]["downgrade"] == 1
    assert s["directions"]["unchanged"] == 1
    assert s["most_overridden_tools"][0] == {"tool": "Fastball", "count": 2}


def test_fields_are_clamped():
    row = audit.record("x" * 200, "y" * 200, "B", "A", "z" * 900, session_id="s" * 200)
    assert len(row["player"]) == 80
    assert len(row["tool"]) == 40
    assert len(row["reason"]) == 300
    assert len(row["session_id"]) == 64


def test_blank_player_and_tool_get_defaults():
    row = audit.record("   ", "  ", "B", "A")
    assert row["player"] == "Unknown"
    assert row["tool"] == "__overall__"


def test_unknown_action_falls_back_to_set():
    assert audit.record("P", "Tool", "B", "A", action="deleted")["action"] == "set"
