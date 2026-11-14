"""Keep scouting text that is not ours to publish out of the public eval folders.

    python -m evaluation.public_guard          # check tracked and staged files

MLB Pipeline excerpts, and the notes built from them, stay on Jake's machine
(decision 2026-11-12). The repo carries only grades, ids, links, labels, and
results. This module is the check behind tests/test_public_eval_data.py; run it
by hand before committing anything under evaluation/pro_benchmark/,
evaluation/independent/, or data/demo_pack/.

The demo pack is the one exception to "no notes": its notes were rewritten in
original wording, and that single file may carry them (REWRITTEN_NOTES). The
excerpt scan still covers it.

A file is a problem if any of these hold:
  1. its name is one of the full files, or it is marked *.local.*
  2. it has a column or JSON key that carries text (excerpt, note_for_app, ...)
  3. a value in it reads like prose: long, many words, not a URL
  4. (only where the full files exist locally) it contains a stretch of an
     actual excerpt
"""
from __future__ import annotations

import csv
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PUBLIC_DIRS = ("evaluation/pro_benchmark", "evaluation/independent", "data/demo_pack")

FULL_FILES = {
    "evaluation/pro_benchmark/pro_benchmark.csv",
    "evaluation/pro_benchmark/pro_benchmark.json",
    "evaluation/independent/queries.json",
    "evaluation/independent/labeling_sheet.csv",
}
TEXT_FIELDS = {"excerpt", "excerpt_verbatim", "note_for_app", "note", "query_note",
               "rickey_text_excerpt", "report", "scouting_report", "text", "scouting_text"}
# The one file allowed to carry notes: the demo pack's notes, rewritten in
# original wording. Only the named key is exempt, and only in this file; the
# excerpt scan still runs over it.
REWRITTEN_NOTES = {"data/demo_pack/demo_notes_rewritten.json": {"scouting_text"}}
# Local-only demo pack file whose text the excerpt scan protects (the scrubbed
# inputs are these same sentences with names and grade words removed).
DEMO_EXCERPTS = "data/demo_pack/demo_excerpts.local.json"
# Explanatory strings that are ours, written about the data, not scouting text.
OUR_PROSE_KEYS = {"_meta", "definitions", "bootstrap", "source_sheet", "note_on_method"}
PROSE_CHARS = 240
PROSE_WORDS = 25
WINDOW = 40


def _is_prose(value: str) -> bool:
    value = value.strip()
    return (len(value) >= PROSE_CHARS and len(value.split()) >= PROSE_WORDS
            and not value.lower().startswith(("http://", "https://")))


def _json_problems(node, path: str = "", allowed: frozenset = frozenset()) -> list[str]:
    problems = []
    if isinstance(node, dict):
        for key, value in node.items():
            here = f"{path}.{key}" if path else str(key)
            if key in OUR_PROSE_KEYS or (key in allowed and isinstance(value, str)):
                continue
            if key.lower() in TEXT_FIELDS:
                problems.append(f"key '{here}' carries scouting text")
            problems += _json_problems(value, here, allowed)
    elif isinstance(node, list):
        for i, value in enumerate(node[:5000]):
            problems += _json_problems(value, f"{path}[{i}]", allowed)
    elif isinstance(node, str) and _is_prose(node):
        problems.append(f"value at '{path}' reads like prose ({len(node)} characters)")
    return problems


def file_problems(path: Path, rel: str) -> list[str]:
    """Why `path` must not be in the public eval folders. Empty list means fine."""
    problems = []
    if rel in FULL_FILES:
        problems.append("this is a full file with scouting text; only the *.public.* copy is committed")
    if ".local." in Path(rel).name:
        problems.append("files marked .local. stay on the local machine")
    suffix = path.suffix.lower()
    try:
        if suffix == ".csv":
            with path.open(newline="", encoding="utf-8-sig") as f:
                reader = csv.DictReader(f)
                columns = reader.fieldnames or []
                for column in columns:
                    if column.strip().lower() in TEXT_FIELDS:
                        problems.append(f"column '{column}' carries scouting text")
                for n, row in enumerate(reader, 2):
                    for column, value in row.items():
                        if isinstance(value, str) and _is_prose(value):
                            problems.append(f"line {n}, column '{column}' reads like prose ({len(value)} characters)")
                            break
                    if len(problems) > 20:
                        break
        elif suffix == ".json":
            allowed = frozenset(REWRITTEN_NOTES.get(rel, ()))
            problems += _json_problems(json.loads(path.read_text(encoding="utf-8")), allowed=allowed)[:20]
    except (UnicodeDecodeError, json.JSONDecodeError, csv.Error) as exc:
        problems.append(f"could not be read as {suffix}: {type(exc).__name__}")
    return problems


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text).lower()


def protected_windows() -> set[str] | None:
    """Stretches of every local excerpt and note, or None where the full files are absent (CI)."""
    bench, queries = ROOT / "evaluation/pro_benchmark/pro_benchmark.json", ROOT / "evaluation/independent/queries.json"
    if not (bench.exists() and queries.exists()):
        return None
    texts = []
    for player in json.loads(bench.read_text())["players"]:
        texts += [player.get("excerpt", ""), player.get("note_for_app", "")]
    for query in json.loads(queries.read_text())["queries"]:
        texts += [query.get("note", ""), query.get("excerpt_verbatim", "")]
    demo = ROOT / DEMO_EXCERPTS                  # the demo pack's source text, where it is present
    if demo.exists():
        for row in json.loads(demo.read_text())["excerpts"]:
            texts += [row.get("excerpt_verbatim", ""), *row.get("skipped_sentences_ranking_fv_draft", [])]
    windows = set()
    for text in filter(None, texts):
        flat = _normalize(text)
        last_start = max(0, len(flat) - WINDOW)
        windows |= {flat[i:i + WINDOW] for i in range(0, last_start + 1, 10)}
        windows.add(flat[last_start:last_start + WINDOW])  # include an unaligned tail too
    return windows


def excerpt_hits(path: Path, windows: set[str]) -> int:
    try:
        body = _normalize(path.read_text(encoding="utf-8-sig"))
    except UnicodeDecodeError:
        return 0
    return sum(window in body for window in windows)


def _git(*args: str) -> list[str]:
    out = subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True, check=True).stdout
    return [line for line in out.splitlines() if line]


def tracked_and_staged() -> list[str]:
    """Every file git has or is about to have under the public eval folders."""
    return sorted(set(_git("ls-files", "--", *PUBLIC_DIRS))
                  | set(_git("diff", "--cached", "--name-only", "--diff-filter=ACMR", "--", *PUBLIC_DIRS)))


def check() -> dict[str, list[str]]:
    """{file: [problems]} for every tracked or staged file that must not be public."""
    windows = protected_windows()
    found = {}
    for rel in tracked_and_staged():
        path = ROOT / rel
        problems = file_problems(path, rel) if path.exists() else []
        if windows and path.exists():
            hits = excerpt_hits(path, windows)
            if hits:
                problems.append(f"contains {hits} stretches of local excerpt text")
        if problems:
            found[rel] = problems
    return found


def main() -> int:
    found = check()
    for rel, problems in found.items():
        for problem in problems:
            print(f"{rel}: {problem}")
    if not found:
        mode = "with" if protected_windows() else "without"
        print(f"public eval folders are clean ({len(tracked_and_staged())} files checked, {mode} the local excerpt scan)")
    return 1 if found else 0


if __name__ == "__main__":
    sys.exit(main())
