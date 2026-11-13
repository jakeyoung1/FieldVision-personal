"""Landing copy stays inside what the code does; /methods discloses the rest."""
import importlib
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.main import app
from backend.services import audit, rag
from evaluation import grade_validation as G
from evaluation import retrieval_eval as E

ROOT = Path(__file__).parent.parent
INDEX = (ROOT / "index.html").read_text()
METHODS = (ROOT / "methods.html").read_text()
client = TestClient(app)


@pytest.fixture(autouse=True)
def temp_db(tmp_path, monkeypatch):
    monkeypatch.setenv("FV_AUDIT_DB", str(tmp_path / "audit.db"))
    importlib.reload(audit)
    yield


def test_overclaims_are_gone_from_the_landing_page():
    for phrase in ("Branch Rickey reports behind every grade",
                   "every quality claim is measured",
                   "handles messy handwriting",
                   "Handwritten Note Upload",
                   "Transform handwritten notes"):
        assert phrase not in INDEX, phrase


def test_landing_page_qualifies_rickey_matches_measurement_and_handwriting():
    assert "most are weak matches, labeled as weak and left out of" in INDEX
    assert "what has not yet" in INDEX and "agreement with coaches, time saved, handwriting transcription" in INDEX
    assert "Transcription accuracy on handwriting" in INDEX and "has not been measured" in INDEX
    assert "Image files are not accepted" in INDEX


def test_landing_pilot_wording_matches_methods():
    assert "A pilot with the staff is planned and has not started." in INDEX
    assert "Planned. Not started." in METHODS
    for claim in ("pilot is underway", "pilot results", "in partnership with", "partnered with", "official partner"):
        assert claim not in INDEX.lower() and claim not in METHODS.lower()


def test_methods_discloses_the_500_character_cut_and_the_code_agrees():
    assert rag.QUERY_CHARS == E.QUERY_CHARS == 500
    assert f"first {rag.QUERY_CHARS} characters" in METHODS
    assert client.get("/api/methods").json()["matching_characters"] == rag.QUERY_CHARS
    source = (ROOT / "backend" / "routes" / "analyze.py").read_text()
    assert "text[:rag.QUERY_CHARS]" in source and "[:500]" not in source


def test_only_the_first_500_characters_affect_matching():
    head = "Cole Braddock\nRHP, junior. Fastball sits 94-96 with ride. Slider is the out pitch. ".ljust(500, ".")
    a = rag.retrieve((head + " power hitting outfielder with a quick bat")[:rag.QUERY_CHARS])
    b = rag.retrieve((head + " knuckleball screwball slow curve")[:rag.QUERY_CHARS])
    assert [h["asset"] for h in a] == [h["asset"] for h in b]


def test_methods_explains_the_four_to_three_mapping_with_both_counts():
    assert 'id="decision-mapping"' in METHODS
    assert "Priority Follow counts as Take" in METHODS
    assert "both Hold and Do Not Follow count as Hold" in METHODS
    m = client.get("/api/methods").json()["decision_mapping"]
    assert m["mapping"] == G.DECISION
    first, second = G.load_ai(), G.load_ai(G.AI_REPEAT)
    assert m["players"] == len(first) == 20
    assert m["four_way_same"] == sum(first[i]["recommendation"] == second[i]["recommendation"] for i in first) == 18
    assert m["three_way_same"] == 18


def test_methods_lists_handwriting_and_matching_window_as_limitations():
    limits = METHODS[METHODS.index('id="limitations"'):]
    assert "Handwriting transcription is unmeasured" in limits
    assert "Matching reads only the opening of the notes" in limits
