"""The demo pack: off by default, a supplement when FV_DEMO_PACK=1, and never evaluation data.

Text written inside these tests ("TOY ...") is made up to exercise the guard.
"""
import csv
import hashlib
import json
import re
import subprocess
import unicodedata
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.main import app
from backend.services import demo_pack, files
from evaluation import public_guard as guard
from evaluation import retrieval_eval

ROOT = Path(__file__).parent.parent
PACK = ROOT / "data" / "demo_pack"
NOTES_REL = "data/demo_pack/demo_notes_rewritten.json"
client = TestClient(app)

TOY_PROSE = ("TOY TEXT, NOT A REAL REPORT. " + "This made-up sentence stands in for a paragraph of scouting "
             "prose so the guard has something long enough to notice. ") * 3


def _notes():
    return json.loads((PACK / "demo_notes_rewritten.json").read_text())["notes"]


def _key():
    return json.loads((PACK / "demo_answer_key.public.json").read_text())["prospects"]


def _inputs():
    with (PACK / "demo_inputs.public.csv").open(newline="", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def _plain(name: str) -> str:
    folded = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z]", "", folded.lower())


def _git(*args):
    return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True)


@pytest.fixture
def flag_on(monkeypatch):
    monkeypatch.setenv("FV_DEMO_PACK", "1")
    demo_pack.load.cache_clear()
    yield
    demo_pack.load.cache_clear()


# ── Flag off: the current demo is all there is ───────────────────────────────
@pytest.mark.parametrize("value", [None, "", "0", "true", "yes", "on", "2"])
def test_flag_off_sends_nothing_from_the_pack(monkeypatch, value):
    if value is None:
        monkeypatch.delenv("FV_DEMO_PACK", raising=False)
    else:
        monkeypatch.setenv("FV_DEMO_PACK", value)
    r = client.get("/api/demo-pack")
    assert r.status_code == 200
    assert r.json() == {"enabled": False}


def test_flag_off_never_reads_the_pack_files(monkeypatch):
    monkeypatch.delenv("FV_DEMO_PACK", raising=False)

    def boom():
        raise AssertionError("the pack was read with the flag off")
    monkeypatch.setattr(demo_pack, "load", boom)
    assert client.get("/api/demo-pack").json() == {"enabled": False}


def test_built_in_demo_is_unchanged_and_separate():
    html = (ROOT / "index.html").read_text()
    assert "fetch('/static/demo/scouting_demo.txt')" in html          # the existing button's source
    assert "⚡ Load demo scouting notes" in html
    demo_text = (ROOT / "static" / "demo" / "scouting_demo.txt").read_text()
    assert hashlib.sha256(demo_text.encode()).hexdigest() == BUILT_IN_DEMO_SHA256
    assert retrieval_eval.DEMO_NOTES == ROOT / "static" / "demo" / "scouting_demo.txt"
    for note in _notes():
        assert note["scouting_text"][:60] not in demo_text


# The built-in demo notes as they were before the pack was wired in. A change
# to that file is a change to the current demo and should be made on purpose.
BUILT_IN_DEMO_SHA256 = "464569c5e3a64fa9602ff8af0b0b11f7358448d02e24be4b71db6a46d879ebaa"


def test_pack_is_not_reachable_as_a_static_file():
    """/static is public whatever the flag says, so the pack must not live there."""
    static = ROOT / "static"
    assert not [p for p in static.rglob("*") if "demo_pack" in p.as_posix() or p.name.startswith("demo_notes")]
    openings = [n["scouting_text"][:60] for n in _notes()]
    for path in static.rglob("*"):
        if path.is_file() and path.suffix in (".txt", ".json", ".csv", ".js", ".md"):
            body = path.read_text(errors="ignore")
            assert not any(o in body for o in openings), path
    for url in ("/static/demo_pack/demo_notes_rewritten.json", "/static/demo/demo_notes_rewritten.json",
                "/data/demo_pack/demo_notes_rewritten.json"):
        assert client.get(url).status_code == 404


# ── Flag on: the pack loads, all of it ───────────────────────────────────────
def test_flag_on_returns_every_prospect(flag_on):
    d = client.get("/api/demo-pack").json()
    ids = [p["id"] for p in d["prospects"]]
    assert d["enabled"] is True
    assert ids == [f"demo{n:02d}" for n in range(1, 17)]
    assert ids == [n["id"] for n in _notes()] == [k["id"] for k in _key()] == [i["id"] for i in _inputs()]
    assert d["count"] == len(ids)
    assert "Never use for evaluation" in d["demo_only"]
    roles = [p["role"] for p in d["prospects"]]
    assert roles.count("pitcher") == roles.count("hitter") == len(ids) // 2


def test_flag_on_pairs_each_note_with_its_public_files(flag_on):
    d = client.get("/api/demo-pack").json()
    notes, key, inputs = ({r["id"]: r for r in rows} for rows in (_notes(), _key(), _inputs()))
    for p in d["prospects"]:
        pid = p["id"]
        assert p["note"] == notes[pid]["scouting_text"]
        assert (p["role"], p["position"], p["level"]) == tuple(inputs[pid][f] for f in ("role", "position", "level"))
        pub = p["published"]
        assert pub["grades"] == key[pid]["grades"] and pub["overall"] == key[pid]["overall"]
        assert pub["grades_line"] == key[pid]["published_grades_line"]
        assert pub["player"] == key[pid]["player"]
        assert pub["source_url"] == inputs[pid]["source_url"] == key[pid]["source_url"]
        assert pub["source_url"].startswith("https://www.mlb.com/")
        # every published grade is the number on the published line
        line = {a.lower(): int(b) for a, b in re.findall(r"(\w+):\s*(\d{2})", pub["grades_line"])}
        assert line == pub["grades"]
        assert set(p) == {"id", "label", "role", "position", "level", "note", "block", "published"}


def test_flag_on_notes_split_into_one_player_each_the_way_the_page_does_it(flag_on):
    d = client.get("/api/demo-pack").json()
    blocks = [b.strip() for b in files.SPLIT_RE.split(d["notes_text"]) if b.strip()]
    assert blocks == [p["block"] for p in d["prospects"]]
    # index.html turns each block into a file named after its first line.
    virtual = []
    for block in blocks:
        first = re.sub(r"[^a-zA-Z0-9 _-]", "", block.split("\n")[0].strip()).strip()
        virtual.append((first + ".txt", block.encode()))
    grouped = files.group_by_player(virtual)
    labels = [p["label"] for p in d["prospects"]]
    assert list(grouped) == labels and len(set(labels)) == len(labels)
    for p in d["prospects"]:
        assert grouped[p["label"]] == p["block"]
        assert p["label"] == f"Demo pack {p['id'][-2:]} {p['position']}"


def test_loader_refuses_files_that_disagree(tmp_path, monkeypatch):
    for name in (demo_pack.NOTES, demo_pack.INPUTS, demo_pack.ANSWER_KEY):
        (tmp_path / name).write_bytes((PACK / name).read_bytes())
    broken = json.loads((tmp_path / demo_pack.NOTES).read_text())
    broken["notes"].pop()
    (tmp_path / demo_pack.NOTES).write_text(json.dumps(broken))
    monkeypatch.setattr(demo_pack, "PACK_DIR", tmp_path)
    demo_pack.load.cache_clear()
    with pytest.raises(ValueError):
        demo_pack.load()
    demo_pack.load.cache_clear()


# ── The notes themselves ─────────────────────────────────────────────────────
GRADE_WORD = re.compile(
    r"(?<![\w-])(?<!batting )(?:plus-plus|double-plus|(?:well[- ])?(?:above|below)[- ]average|fringe[- ]average|"
    r"solid[- ]average|fringy|fringe|average|plus|solid|elite(?:-level)?|top-of-the-scale)(?![\w-])", re.I)
SCALE = r"(?:20|25|30|35|40|45|50|55|60|65|70|75|80)"
# A number on the 20-80 scale may only appear as a measurement or a count.
BARE_SCALE_NUMBER = re.compile(rf"(?<![\d.,/$-]){SCALE}(?![\d.,/]|s\b|\s*(?:mph|rpm|hits|games|steals|homers)|-(?:mph|foot))")
GRADE_PHRASE = re.compile(rf"\b{SCALE}(?:-| )grade\b|\bgrades? of {SCALE}\b|\bgrade\b", re.I)


def test_notes_carry_no_names_grades_or_scrub_markers():
    key = {k["id"]: k for k in _key()}
    name_parts = {part for k in key.values() for part in re.findall(r"[^\W\d_]{3,}", k["player"])}
    for note in _notes():
        text = note["scouting_text"]
        assert 200 <= len(text) <= 330, note["id"]
        assert not [w for w in name_parts if re.search(rf"\b{re.escape(w)}\b", text)], note["id"]
        assert not GRADE_WORD.search(text), (note["id"], GRADE_WORD.search(text))
        assert not GRADE_PHRASE.search(text), note["id"]
        assert not BARE_SCALE_NUMBER.search(text), (note["id"], BARE_SCALE_NUMBER.search(text))
        assert "[" not in text and "removed" not in text, note["id"]
        assert not re.search(r"\b(?:prospect|ranked|top 100|ceiling|upside)\b", text, re.I), note["id"]


def test_public_files_are_the_text_free_ones():
    with (PACK / "demo_inputs.public.csv").open(newline="", encoding="utf-8-sig") as f:
        input_columns = csv.DictReader(f).fieldnames
    with (PACK / "demo_answer_key.public.csv").open(newline="", encoding="utf-8-sig") as f:
        key_columns = csv.DictReader(f).fieldnames
    for columns in (input_columns, key_columns):
        assert not {c.lower() for c in columns} & guard.TEXT_FIELDS
    committed = {p.name for p in PACK.iterdir() if ".local." not in p.name and p.name != ".DS_Store"}
    assert committed == {".gitignore", "demo_notes_rewritten.json", "demo_inputs.public.csv",
                         "demo_answer_key.public.csv", "demo_answer_key.public.json"}


# ── The public guard covers the pack ─────────────────────────────────────────
def test_guard_watches_the_pack_folder_and_passes_the_committed_files():
    assert "data/demo_pack" in guard.PUBLIC_DIRS
    for path in sorted(PACK.iterdir()):
        if path.suffix in (".csv", ".json") and ".local." not in path.name:
            rel = path.relative_to(ROOT).as_posix()
            assert guard.file_problems(path, rel) == [], rel
    if _git("rev-parse", "--is-inside-work-tree").returncode == 0:
        assert guard.check() == {}


def test_guard_allows_notes_only_in_the_one_rewritten_file(tmp_path):
    notes = PACK / "demo_notes_rewritten.json"
    assert guard.file_problems(notes, NOTES_REL) == []
    for rel in ("data/demo_pack/other_notes.json", "evaluation/pro_benchmark/demo_notes_rewritten.json",
                "evaluation/independent/demo_notes_rewritten.json"):
        assert any("scouting_text" in p for p in guard.file_problems(notes, rel)), rel


def test_guard_still_flags_other_text_inside_the_rewritten_file(tmp_path):
    doc = {"_meta": {"what": "toy"}, "notes": [{"id": "x1", "scouting_text": "TOY note", "excerpt_verbatim": "TOY"}]}
    path = tmp_path / "a.json"
    path.write_text(json.dumps(doc))
    assert any("excerpt_verbatim" in p for p in guard.file_problems(path, NOTES_REL))
    doc = {"notes": [{"id": "x1", "scouting_text": "TOY note", "summary_line": TOY_PROSE}]}
    path.write_text(json.dumps(doc))
    assert any("reads like prose" in p for p in guard.file_problems(path, NOTES_REL))
    doc = {"notes": [{"id": "x1", "scouting_text": {"nested": TOY_PROSE}}]}           # only a plain string is exempt
    path.write_text(json.dumps(doc))
    assert guard.file_problems(path, NOTES_REL)


def test_guard_flags_the_packs_local_files_and_git_ignores_them(tmp_path):
    path = tmp_path / "x.json"
    path.write_text(json.dumps({"inputs": [{"id": "x1", "scouting_text": "TOY"}]}))
    for name in ("demo_inputs.local.json", "demo_excerpts.local.json", "demo_inputs.local.csv"):
        assert guard.file_problems(path, f"data/demo_pack/{name}"), name
    if _git("rev-parse", "--is-inside-work-tree").returncode != 0:
        pytest.skip("not a git checkout")
    for name in ("demo_inputs.local.json", "demo_excerpts.local.json", "demo_inputs.local.csv"):
        rel = f"data/demo_pack/{name}"
        assert _git("check-ignore", "-q", rel).returncode == 0, f"{rel} is not ignored"
        assert _git("ls-files", "--error-unmatch", rel).returncode != 0, f"{rel} is tracked"
    assert _git("check-ignore", "-q", NOTES_REL).returncode != 0


def test_rewritten_notes_share_no_stretch_with_the_local_source_text():
    """Runs where the local excerpts exist (never in CI). Nothing is written to disk."""
    local = ROOT / guard.DEMO_EXCERPTS
    if not local.exists() or guard.protected_windows() is None:
        pytest.skip("the pack's source excerpts are local only")
    windows = guard.protected_windows()
    for row in json.loads(local.read_text())["excerpts"]:
        assert guard._normalize(row["excerpt_verbatim"])[:guard.WINDOW] in windows      # the scan covers the pack
    assert guard.excerpt_hits(PACK / "demo_notes_rewritten.json", windows) == 0
    for name in ("demo_inputs.public.csv", "demo_answer_key.public.csv", "demo_answer_key.public.json"):
        assert guard.excerpt_hits(PACK / name, windows) == 0, name


# ── Demo only: no overlap with anything used to measure the app ──────────────
def test_pack_shares_no_player_with_the_benchmark_or_the_independent_set():
    with (ROOT / "evaluation/pro_benchmark/pro_benchmark.public.csv").open(newline="", encoding="utf-8-sig") as f:
        bench = list(csv.DictReader(f))
    queries = json.loads((ROOT / "evaluation/independent/queries.public.json").read_text())["queries"]
    taken_names = {_plain(r["player"]) for r in bench} | {_plain(q["player"]) for q in queries}
    taken_ids = {r["mlb_person_id"].strip() for r in bench}
    taken_urls = {r["source_url"] for r in bench} | {q["source_url"] for q in queries}
    for k in _key():
        assert _plain(k["player"]) not in taken_names, k["id"]
        assert str(k["mlb_person_id"]) not in taken_ids, k["id"]
        assert k["source_url"] not in taken_urls, k["id"]
    assert len({_plain(k["player"]) for k in _key()}) == len(_key())


def test_pack_is_absent_from_evaluation_data_and_the_rickey_corpus():
    names = [k["player"] for k in _key()]
    openings = [n["scouting_text"][:60] for n in _notes()]
    targets = [p for p in (ROOT / "evaluation").rglob("*") if p.is_file() and p.suffix in (".csv", ".json", ".md")]
    targets.append(ROOT / "data" / "branch-rickey-scouting.csv")
    for path in targets:
        body = path.read_text(encoding="utf-8", errors="ignore")
        assert not re.search(r"\bdemo\d{2}\b", body), path
        assert not [n for n in names if n in body], path
        assert not [o for o in openings if o in body], path


# ── The page ─────────────────────────────────────────────────────────────────
def test_page_adds_the_pack_only_from_the_api_and_types_nothing_from_it():
    html = (ROOT / "index.html").read_text()
    start = html.index("async function injectDemoPack")
    block = html[start:html.index("// ── Trackman charts", start)]
    assert "fetch('/api/demo-pack')" in block and "pack.enabled" in block
    assert "fv-demo-pack-btn" in block and "fv-demo-pack-panel" in block
    assert "Demo comparison, not an evaluation" in block
    assert "innerHTML" not in block                                   # pack text is set as text, never parsed
    assert "${pack.count}" in block and str(len(_key())) not in re.sub(r"[\d.]+(?:px|rem)|rgba\([^)]*\)|#[0-9a-f]{6}", "", block)
    for k in _key():
        assert k["player"] not in html and k["published_grades_line"] not in html
    for note in _notes():
        assert note["scouting_text"][:60] not in html
