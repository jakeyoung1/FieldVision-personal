"""POST /api/compare — head-to-head AI comparison of two scouted players."""
import json
import re

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from backend.services import claude

router = APIRouter()

SYSTEM_COMPARE = claude.SYSTEM_SCOUT + """

Mode: HEAD-TO-HEAD COMPARISON. You compare exactly two players from their scouting
reports and return ONLY valid JSON (no markdown fences) with this shape:
{"verdict": "2-3 sentence overall verdict naming the preferred player and why",
 "preferred": "player A name or player B name",
 "categories": [{"name": "Hitting|Power|Speed|Arm|Field|Pitching|Command|Stuff|Makeup|Upside (pick 4-6 relevant)",
   "a": "one-line assessment of player A", "b": "one-line assessment of player B",
   "edge": "A" | "B" | "even"}],
 "risk": {"a": "one-line risk profile", "b": "one-line risk profile"}}
Ground every claim in the reports. If a category isn't covered for a player, say so."""


class CompareRequest(BaseModel):
    player_a: dict   # {label, report}
    player_b: dict


@router.post("/compare")
async def compare(req: CompareRequest):
    a_label = str(req.player_a.get("label", "Player A"))[:60]
    b_label = str(req.player_b.get("label", "Player B"))[:60]
    a_report = str(req.player_a.get("report", ""))[:6000]
    b_report = str(req.player_b.get("report", ""))[:6000]
    if not a_report.strip() or not b_report.strip():
        raise HTTPException(400, "Both players need scouting reports")

    prompt = f"""PLAYER A — {a_label}:
{a_report}

PLAYER B — {b_label}:
{b_report}

Compare them head to head."""

    try:
        client = claude._client()
        resp = client.messages.create(
            model=claude.MODEL,
            max_tokens=1000,
            system=SYSTEM_COMPARE,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = resp.content[0].text.strip()
        raw = re.sub(r"^```(?:json)?\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw)
        try:
            result = json.loads(raw)
        except json.JSONDecodeError:
            m = re.search(r"\{.*\}", raw, re.DOTALL)
            if not m:
                raise HTTPException(502, "Comparison did not return valid JSON")
            result = json.loads(m.group())
        result["a_label"] = a_label
        result["b_label"] = b_label
        return JSONResponse(result)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, f"Server error: {str(e)}")
