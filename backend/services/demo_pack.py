"""The 16-prospect demo pack: a supplement to the built-in demo, off by default.

Set FV_DEMO_PACK=1 to turn it on. With the flag off nothing here is read and
the page shows only the built-in demo.

DEMO ONLY. These prospects exist to show the app on varied, realistic input.
They are never used for evaluation, cutoff or retrieval tuning, the gold set,
or the Rickey corpus; tests/test_demo_pack.py keeps them out of those files.

What is committed in data/demo_pack/:
  demo_notes_rewritten.json    notes rewritten in original wording (no names, no grades)
  demo_inputs.public.csv       id, role, position, level, publisher, source link
  demo_answer_key.public.json  the published grades and where they were published
The source excerpts are not in the repo and are not read here.
"""
from __future__ import annotations

import csv
import json
import os
from functools import lru_cache
from pathlib import Path

PACK_DIR = Path(__file__).resolve().parents[2] / "data" / "demo_pack"
NOTES = "demo_notes_rewritten.json"
INPUTS = "demo_inputs.public.csv"
ANSWER_KEY = "demo_answer_key.public.json"
SEPARATOR = "\n---\n"          # the page's own rule for "next player"


def enabled() -> bool:
    """Read on every call so the flag can change without a code change."""
    return os.environ.get("FV_DEMO_PACK", "").strip() == "1"


def label(prospect_id: str, position: str) -> str:
    """The first line of a note block. The page turns it into the player label,
    keeping only letters, digits, spaces, hyphens, and underscores."""
    return f"Demo pack {prospect_id.removeprefix('demo')} {position}"


@lru_cache(maxsize=1)
def load() -> dict:
    """Join the three committed files on id. Raises if they disagree."""
    notes_file = json.loads((PACK_DIR / NOTES).read_text(encoding="utf-8"))
    key_file = json.loads((PACK_DIR / ANSWER_KEY).read_text(encoding="utf-8"))
    with (PACK_DIR / INPUTS).open(newline="", encoding="utf-8-sig") as f:
        inputs = {row["id"]: row for row in csv.DictReader(f)}
    notes = {row["id"]: row for row in notes_file["notes"]}
    key = {row["id"]: row for row in key_file["prospects"]}
    if not (list(notes) == list(inputs) == list(key)):
        raise ValueError("demo pack files do not list the same ids in the same order")

    prospects = []
    for pid, meta in inputs.items():
        note, published = notes[pid], key[pid]
        for field in ("role", "position", "level"):
            if not (note[field] == meta[field] == published[field]):
                raise ValueError(f"demo pack files disagree on {field} for {pid}")
        name = label(pid, meta["position"])
        prospects.append({
            "id": pid,
            "label": name,
            "role": meta["role"],
            "position": meta["position"],
            "level": meta["level"],
            "note": note["scouting_text"],
            "block": f"{name}\n{note['scouting_text']}",
            "published": {
                "player": published["player"],
                "publisher": meta["publisher"],
                "source": published["source"],
                "source_url": meta["source_url"],
                "date_checked": meta["date_checked"],
                "grades_line": published["published_grades_line"],
                "grades": published["grades"],
                "overall": published["overall"],
            },
        })
    return {
        "demo_only": notes_file["_meta"]["DEMO_ONLY"],
        "notes_status": notes_file["_meta"]["status"],
        "count": len(prospects),
        "prospects": prospects,
        "notes_text": SEPARATOR.join(p["block"] for p in prospects),
    }
