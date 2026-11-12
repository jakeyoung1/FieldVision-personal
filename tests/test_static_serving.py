"""Unknown URLs get real 404s and never expose files. No API key."""
import pytest
from fastapi.testclient import TestClient

from backend.main import ROOT_HTML, app

client = TestClient(app)
INDEX = ROOT_HTML.read_text(encoding="utf-8")


@pytest.mark.parametrize("path", [
    "/.env.example",
    "/backend/main.py",
    "/requirements.txt",
    "/.git/config",
    "/fieldvision_audit.db",
    "/..%2f..%2f..%2f..%2fetc%2fhostname",
    "/%2e%2e/%2e%2e/%2e%2e/%2e%2e/etc/hostname",
])
def test_sensitive_paths_are_404_and_never_file_contents(path):
    r = client.get(path)
    assert r.status_code == 404
    assert "FV_" not in r.text and "import " not in r.text and "[core]" not in r.text
    assert "That page isn't in the lineup" in r.text


@pytest.mark.parametrize("path", ["/no-such-page", "/players/123", "/methods/extra"])
def test_unknown_pages_get_the_not_found_page(path):
    r = client.get(path)
    assert r.status_code == 404
    assert r.headers["content-type"].startswith("text/html")
    assert r.text != INDEX
    assert 'href="/"' in r.text and 'href="/methods"' in r.text


def test_unknown_api_routes_get_json_404():
    r = client.get("/api/no-such-route")
    assert r.status_code == 404
    assert r.json() == {"detail": "No such API route"}


def test_real_pages_still_200():
    assert client.get("/").text == INDEX
    assert client.get("/methods").status_code == 200
    assert client.head("/").status_code == 200
    assert client.get("/api/health").json()["status"] == "ok"


def test_static_assets_still_served():
    r = client.get("/static/logo.svg")
    assert r.status_code == 200
    assert "<svg" in r.text


def test_static_mount_blocks_traversal():
    r = client.get("/static/..%2f.env.example")
    assert r.status_code == 404
