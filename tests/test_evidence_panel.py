"""Rickey matches reach every report, and the site's copy matches the code.

No API key required: the model call is replaced with a fixed evaluation.
"""
import re
from pathlib import Path

from fastapi.testclient import TestClient

from backend.main import app
from backend.services import scout_report

HTML = (Path(__file__).parent.parent / "index.html").read_text()
client = TestClient(app)

NOTES = ("Cole Braddock\nRHP, junior, 6'3 205. Fastball sits 94-96, touched 97 twice this spring. "
         "Slider is the out pitch, mid-80s with late two-plane bite. Changeup flashes average.")


def _fake_evaluate(notes, rag_context="", trackman_context=""):
    return scout_report.finalize({
        "name": "Cole Braddock", "position": "RHP", "grade": "B+", "summary": "s",
        "tools": [{"name": "Fastball", "grade": "A-", "evidence": ["Fastball sits 94-96"],
                   "interpretation": "i", "projection": "p"}],
        "recommendation": "Follow", "recommendation_reason": "r", "what_would_change": [],
    }, notes, had_measurement=False)


def test_every_report_carries_five_scored_rickey_matches(monkeypatch):
    monkeypatch.setattr(scout_report, "evaluate", _fake_evaluate)
    r = client.post("/api/analyze", files={"files_upload": ("braddock.txt", NOTES.encode(), "text/plain")})
    assert r.status_code == 200
    result = r.json()["results"][0]
    matches = result["rickey"]["matches"]
    assert len(matches) == 5
    for m in matches:
        assert 0 < m["score"] <= 1
        assert m["confidence"] in ("strong", "moderate", "weak")
        assert m["text"] and m["source"] and m["scan_url"].startswith("http")
    assert result["rickey"]["used_as_evidence"] == sum(m["confidence"] != "weak" for m in matches)


def test_panel_renders_scores_and_expandable_excerpts():
    fn = HTML[HTML.index("function rickeyNotice"):HTML.index("function outcomeFlags")]
    assert "<details" in fn and "<summary" in fn
    assert "h.score.toFixed(2)" in fn
    assert "not used" in fn and "given to the model" in fn
    assert "escAudit(text)" in fn   # archive text is escaped before it is inserted


def test_pipeline_copy_matches_what_the_code_produces():
    pipeline = HTML[HTML.index('<section id="pipeline"'):HTML.index("</section>", HTML.index('<section id="pipeline"'))]
    # Removed claims: the Evidence Chain does not produce these.
    assert "two actionable recommendations" not in pipeline
    assert "Branch Rickey parallel insight" not in pipeline
    assert "photo" not in pipeline.lower()
    # Recommendations named in the copy are ones the report can return.
    for rec in re.findall(r"Priority Follow|Follow|Hold", pipeline):
        assert rec in scout_report.RECOMMENDATIONS
    # File types named in the copy are the ones the upload accepts.
    accept = re.search(r'id="fv-file-input"[^>]*accept="([^"]+)"', HTML).group(1)
    for ext in ("TXT", "MD", "PDF"):
        assert ext in pipeline and f".{ext.lower()}" in accept


def test_no_leftover_internal_or_false_copy():
    for phrase in ("now live and approved", "1919–1945", "This fork", "group deployment",
                   "benchmarked against decades", "Production-grade", "RAG Hits"):
        assert phrase not in HTML, phrase
    assert 'href="https://github.com/jakeyoung1/FieldVision"' not in HTML
