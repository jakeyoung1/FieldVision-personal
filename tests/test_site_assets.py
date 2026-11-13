"""Favicon, robots.txt, locally compiled Tailwind, and a portable start script."""
import re
import subprocess
from pathlib import Path

from fastapi.testclient import TestClient

from backend.main import app

ROOT = Path(__file__).parent.parent
INDEX = (ROOT / "index.html").read_text()
client = TestClient(app)


def test_favicon_is_served_as_a_real_icon():
    r = client.get("/favicon.ico")
    assert r.status_code == 200
    assert r.headers["content-type"] == "image/x-icon"
    assert r.content[:4] == b"\x00\x00\x01\x00"          # ICO file signature
    for page in ("index.html", "methods.html", "not_found.html"):
        assert 'rel="icon"' in (ROOT / page).read_text(), page


def test_robots_txt_allows_pages_and_keeps_crawlers_off_the_api():
    r = client.get("/robots.txt")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/plain")
    lines = r.text.splitlines()
    assert lines[0] == "User-agent: *"
    assert "Allow: /" in lines and "Disallow: /api/" in lines


def test_tailwind_is_compiled_locally_not_loaded_from_the_cdn():
    assert "cdn.tailwindcss.com" not in INDEX
    assert "tailwind.config =" not in INDEX
    css = (ROOT / "static" / "css" / "tailwind.css").read_text()
    assert ".scroll-smooth{scroll-behavior:smooth}" in css     # the one utility the page uses
    assert "box-sizing:border-box" in css                      # Preflight, which the layout relies on
    # Linked after the page's own <style>, the order the CDN build produced.
    head = INDEX[:INDEX.index("</head>")]
    assert head.rindex("</style>") < head.index('href="static/css/tailwind.css"')
    assert client.get("/static/css/tailwind.css").status_code == 200


def test_no_script_is_loaded_from_another_origin():
    assert re.findall(r'<script[^>]+src="https?://', INDEX) == []


def test_build_inputs_are_committed_and_version_pinned():
    build = (ROOT / "scripts" / "build_css.sh").read_text()
    assert re.search(r'TAILWIND_VERSION="\d+\.\d+\.\d+"', build)
    assert (ROOT / "tailwind.config.js").exists() and (ROOT / "scripts" / "tailwind.input.css").exists()


def test_start_script_is_portable():
    script = (ROOT / "start.sh").read_text()
    assert "/Users/" not in script and "/home/" not in script
    command = [line for line in script.splitlines() if line.startswith("exec ")]
    assert len(command) == 1
    assert "--reload" not in command[0]                 # opt-in, passed through "$@"
    assert '"$@"' in command[0] and "${PORT:-8000}" in command[0]
    assert subprocess.run(["bash", "-n", str(ROOT / "start.sh")]).returncode == 0
