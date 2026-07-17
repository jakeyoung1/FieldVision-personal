"""Evidence Chain validation — confidence rules, evidence verification, markdown. No API key."""
from backend.services.scout_report import _confidence, finalize, to_markdown

NOTES = "Fastball sits 94-96 with late life. Slider flashes plus. Command wanders when he overthrows."


def _tool(evidence, measurement=None, conflict=None):
    return {"name": "Fastball", "grade": "B+", "evidence": evidence,
            "measurement": measurement, "interpretation": "above average",
            "projection": "could miss bats", "conflict": conflict}


def test_confidence_rules():
    assert _confidence(2, True) == "High"
    assert _confidence(3, False) == "Medium"
    assert _confidence(1, True) == "Medium"
    assert _confidence(1, False) == "Low"
    assert _confidence(0, False) == "Low"


def test_evidence_verified_against_notes():
    data = {"tools": [_tool(["Fastball sits 94-96 with late life", "invented quote not in notes"])]}
    out = finalize(data, NOTES, had_measurement=False)
    t = out["tools"][0]
    assert t["evidence_verified"] is True
    assert "Fastball sits 94-96 with late life" in t["evidence"]


def test_measurement_stripped_without_trackman():
    data = {"tools": [_tool(["Fastball sits 94-96 with late life"], measurement="94.2 mph avg",
                            conflict="notes say X data says Y")]}
    out = finalize(data, NOTES, had_measurement=False)
    assert out["tools"][0]["measurement"] is None
    assert out["tools"][0]["conflict"] is None   # no conflict allowed without data


def test_conflict_kept_with_measurement():
    data = {"tools": [_tool(["Slider flashes plus"], measurement="2400 rpm", conflict="movement is average")]}
    out = finalize(data, NOTES, had_measurement=True)
    assert out["tools"][0]["conflict"] == "movement is average"
    assert out["tools"][0]["confidence"] == "Medium"   # 1 quote + measurement


def test_invalid_grade_and_recommendation_defaulted():
    data = {"grade": "S+", "recommendation": "Draft Immediately",
            "tools": [{"name": "Hit", "grade": "Z", "evidence": []}]}
    out = finalize(data, NOTES, had_measurement=False)
    assert out["grade"] == "C"
    assert out["recommendation"] == "Follow"
    assert out["tools"][0]["grade"] == "C"


def test_markdown_render_contains_chain():
    data = {"grade": "B+", "summary": "Good arm.", "recommendation": "Priority Follow",
            "recommendation_reason": "premium velo", "risk": "command",
            "what_would_change": ["50 more pitches"],
            "tools": [_tool(["Fastball sits 94-96 with late life"], measurement="94.5 mph")]}
    md = to_markdown(finalize(data, NOTES, had_measurement=True))
    assert "## Tools" in md
    assert 'Evidence: "Fastball sits 94-96 with late life"' in md
    assert "Measurement: 94.5 mph" in md
    assert "Priority Follow" in md
    assert "What Would Change This Evaluation" in md
