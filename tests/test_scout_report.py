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


# ── Evidence verification: whole quote, whitespace-tolerant ──────────────────

def test_quote_with_verbatim_prefix_and_invented_tail_is_not_verified():
    """The regression this fix exists for.

    The earlier check tested only `quote[:60]`, so a quote that reproduced the
    first 60 characters of the notes and then invented the rest was marked
    verified. NOTES[:60] is "Fastball sits 94-96 with late life. Slider flashes
    plus. Com", so the string below cleared the old test and fails the new one.
    """
    drifted = "Fastball sits 94-96 with late life. Slider flashes plus. Com and he throws 105 mph"
    assert drifted.casefold()[:60] in NOTES.casefold()      # old check passed it
    data = {"tools": [_tool([drifted])]}
    out = finalize(data, NOTES, had_measurement=False)
    t = out["tools"][0]
    assert t["evidence_verified"] is False
    assert t["evidence"] == [drifted]          # surfaced, but flagged unverified
    assert t["confidence"] == "Low"


def test_plainly_invented_quote_is_not_verified():
    data = {"tools": [_tool(["best changeup in the country"])]}
    out = finalize(data, NOTES, had_measurement=False)
    assert out["tools"][0]["evidence_verified"] is False


def test_quote_spanning_a_line_break_still_verifies():
    """PDF extraction wraps lines; verbatim text should survive that."""
    notes = "Fastball sits 94-96\nwith late life. Slider flashes plus."
    data = {"tools": [_tool(["Fastball sits 94-96 with late life"])]}
    out = finalize(data, notes, had_measurement=False)
    assert out["tools"][0]["evidence_verified"] is True


def test_smart_quotes_and_trailing_ellipsis_do_not_block_verification():
    data = {"tools": [_tool(["“Slider flashes plus…”"])]}
    out = finalize(data, NOTES, had_measurement=False)
    assert out["tools"][0]["evidence_verified"] is True


def test_empty_quote_is_never_verified():
    data = {"tools": [_tool(["   ", "Slider flashes plus"])]}
    out = finalize(data, NOTES, had_measurement=False)
    assert out["tools"][0]["evidence"] == ["Slider flashes plus"]
