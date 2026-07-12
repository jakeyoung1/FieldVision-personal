"""Basketball Claude service — scouting analysis, box score interpretation, coaching modes."""
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
