"""SPA fallback must never expose files outside /static. No API key."""
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
    "/..%2f..%2f..%2f..%2fetc%2fhostname",
    "/%2e%2e/%2e%2e/%2e%2e/%2e%2e/etc/hostname",
])
def test_unknown_paths_serve_index_not_files(path):
    r = client.get(path)
    assert r.status_code == 200
    assert r.text == INDEX


def test_static_assets_still_served():
    r = client.get("/static/logo.svg")
    assert r.status_code == 200
    assert "<svg" in r.text


def test_static_mount_blocks_traversal():
    r = client.get("/static/..%2f.env.example")
    assert r.status_code == 404
