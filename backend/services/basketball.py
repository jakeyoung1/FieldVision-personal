"""Basketball Claude service — scouting analysis, box score interpretation, coaching modes."""
import json
import re

from backend.services import claude

SYSTEM_SCOUT = """You are FieldVision Hoops, an AI basketball scouting assistant. Analyze player
notes with precision — shooting mechanics, athleticism, defensive versatility, basketball IQ,
positional fit. Be concise, insightful, and use basketball terminology naturally."""

SYSTEM_COACH_BASE = """You are FieldVision Hoops, a basketball coaching assistant for a college
coaching staff. Speak plainly — coaches need actionable insights, not jargon."""

COACH_MODES = {
    "development": SYSTEM_COACH_BASE + """

Mode: PLAYER DEVELOPMENT. Given a player's scouting notes or stats, produce a development plan:
## Current Profile
## Priority Skill Areas (ranked, max 3)
## Drill Recommendations (specific drills per skill area)
## 4-Week Focus (what to emphasize each week)
Keep it practical for a college practice environment.""",

    "opponent": SYSTEM_COACH_BASE + """

Mode: OPPONENT SCOUTING. Given opponent box scores or notes, produce a game-prep report:
## Opponent Tendencies
## Key Players & How to Guard Them
## Offensive Keys
## Defensive Keys
## X-Factor
Ground every claim in the provided data — don't invent stats.""",

    "practice": SYSTEM_COACH_BASE + """

Mode: PRACTICE PLANNING. Given team weaknesses and available time, produce a practice plan
with time-blocked segments (warmup, skill work, team concepts, competitive periods,
conditioning). Format each block as "MM min — Activity: purpose". Total must fit the
stated duration; if none stated, plan for 90 minutes.""",

    "chat": SYSTEM_COACH_BASE + """

Mode: OPEN CHAT. Answer basketball coaching and scouting questions conversationally,
using any session context provided.""",
}


def analyze_notes(text: str, context: str = "") -> str:
    """Analyze raw basketball scouting notes and return a structured report."""
    context_block = f"REFERENCE CONTEXT:\n{context}\n" if context else ""
    prompt = f"""Analyze these basketball scouting notes and produce a structured report.

{context_block}
SCOUTING NOTES:
{text}

Format your response as:
## Player Overview
## Key Strengths
## Areas of Concern
## Statistical Comparison (if context available)
## Recommendation & Grade (use full plus/minus scale: A, A-, B+, B, B-, C+, C, C-, D+, D, F)"""

    client = claude._client()
    resp = client.messages.create(
        model=claude.MODEL,
        max_tokens=1200,
        system=SYSTEM_SCOUT,
        messages=[{"role": "user", "content": prompt}],
    )
    return resp.content[0].text


def interpret_box_score(summary: str, focus: str = "") -> str:
    """Translate box score stat lines into plain-language coach explanation."""
    focus_line = f"Focus area: {focus}" if focus else ""
    prompt = f"""You are a basketball analyst presenting box score data objectively.
This data may include players from multiple teams. Describe each player's production factually —
scoring, efficiency, rebounding, playmaking, defense. Use neutral third-person language
(e.g. "Smith averaged..." not "our guy" or "we need"). 2-3 sentences per player.
End with one cross-dataset observation.
{focus_line}

DATA SUMMARY:
{summary}"""

    client = claude._client()
    resp = client.messages.create(
        model=claude.MODEL,
        max_tokens=600,
        system=SYSTEM_COACH_BASE,
        messages=[{"role": "user", "content": prompt}],
    )
    return resp.content[0].text


COURT_SYSTEM = SYSTEM_COACH_BASE + """

Mode: PLAY DESIGN. You design a single half-court set play as animation keyframes.

COURT COORDINATE SYSTEM (offensive half court):
- x: 0.0 = left sideline, 1.0 = right sideline
- y: 0.0 = baseline (behind the hoop), 1.0 = half-court line
- Hoop is at (0.50, 0.06). Free-throw line center: (0.50, 0.40). Paint: x 0.34-0.66, y 0.0-0.40.
- Three-point corners: x < 0.10 or x > 0.90 with y < 0.30. Top of the arc: (0.50, 0.55).

RULES:
- Design around each player's listed strengths (shooters end up at the arc, cutters cut, screeners screen, playmakers handle the ball).
- 3 to 7 keyframes per player, t in seconds from 0 to duration (6-10 s). Every player starts at their given position at t=0.
- Movement between keyframes is linear — add intermediate keyframes for curved cuts.
- The ball must always be held by a player whose position at that time matches the ball position, until a "shot" at the end.
- Passes must be between players reasonably near the passing lane. End the play with a scoring action.
- action: short label shown to coaches ("sets back screen", "curl cut off screen", "catch and shoot").

Return ONLY valid JSON, no markdown fences, exactly this shape:
{"play_name": "string", "description": "2-3 sentence coach summary", "duration": 8,
 "players": [{"id": "P1", "role": "string",
   "keyframes": [{"t": 0, "x": 0.5, "y": 0.8, "action": "optional string"}]}],
 "ball": {"keyframes": [{"t": 0, "x": 0.5, "y": 0.8, "holder": "P1"}]}}"""


def design_play(players: list[dict], objective: str = "") -> dict:
    """Generate an animated set play as keyframe JSON from player strengths."""
    roster = "\n".join(
        f"- id {p['id']}: {p.get('name') or 'Player ' + p['id']} "
        f"({p.get('position') or 'unknown position'}) — strengths: "
        f"{p.get('strengths') or 'unknown'}; starts at (x={float(p['x']):.2f}, y={float(p['y']):.2f})"
        for p in players
    )
    objective_line = f"\nCOACH'S OBJECTIVE: {objective}" if objective else ""
    prompt = f"""Design one half-court set play for this lineup.{objective_line}

LINEUP (use these exact ids and starting positions):
{roster}"""

    client = claude._client()
    resp = client.messages.create(
        model=claude.MODEL,
        max_tokens=2500,
        system=COURT_SYSTEM,
        messages=[{"role": "user", "content": prompt}],
    )
    raw = resp.content[0].text.strip()
    raw = re.sub(r"^```(?:json)?\s*", "", raw)
    raw = re.sub(r"\s*```$", "", raw)
    try:
        play = json.loads(raw)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", raw, re.DOTALL)
        if not m:
            raise ValueError("Model did not return valid play JSON")
        play = json.loads(m.group())
    return _clean_play(play, players)


def _clean_play(play: dict, players: list[dict]) -> dict:
    """Clamp coordinates, sort keyframes, guarantee every player id is present."""
    def _clamp_kfs(kfs):
        out = []
        for kf in kfs or []:
            try:
                out.append({
                    "t": max(0.0, float(kf["t"])),
                    "x": min(0.98, max(0.02, float(kf["x"]))),
                    "y": min(0.98, max(0.02, float(kf["y"]))),
                    **({"action": str(kf["action"])[:80]} if kf.get("action") else {}),
                    **({"holder": str(kf["holder"])} if kf.get("holder") else {}),
                })
            except (KeyError, TypeError, ValueError):
                continue
        return sorted(out, key=lambda k: k["t"])

    duration = min(15.0, max(4.0, float(play.get("duration", 8))))
    by_id = {str(p.get("id")): _clamp_kfs(p.get("keyframes")) for p in play.get("players", [])}
    roles = {str(p.get("id")): str(p.get("role", ""))[:60] for p in play.get("players", [])}

    out_players = []
    for p in players:
        pid = p["id"]
        kfs = by_id.get(pid) or []
        if not kfs:
            kfs = [{"t": 0.0, "x": float(p["x"]), "y": float(p["y"])}]
        out_players.append({"id": pid, "role": roles.get(pid, ""), "keyframes": kfs})

    ball_kfs = _clamp_kfs((play.get("ball") or {}).get("keyframes"))
    if not ball_kfs:
        first = out_players[0]["keyframes"][0]
        ball_kfs = [{"t": 0.0, "x": first["x"], "y": first["y"], "holder": out_players[0]["id"]}]

    return {
        "play_name": str(play.get("play_name", "Set Play"))[:80],
        "description": str(play.get("description", ""))[:500],
        "duration": duration,
        "players": out_players,
        "ball": {"keyframes": ball_kfs},
    }


def coach_reply(mode: str, history: list[dict], context: str = "") -> str:
    """Coaching conversation in one of four modes: development, opponent, practice, chat."""
    system = COACH_MODES.get(mode, COACH_MODES["chat"])
    if context:
        system += f"\n\nSession context (scouting reports and box score data loaded this session):\n{context}"

    client = claude._client()
    resp = client.messages.create(
        model=claude.MODEL,
        max_tokens=1200,
        system=system,
        messages=history,
    )
    return resp.content[0].text
