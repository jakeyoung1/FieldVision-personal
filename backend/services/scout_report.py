"""Structured scouting evaluation — the Evidence Chain.

One Claude call returns a fully structured evaluation whose every tool grade
carries four explicit layers:
  observation (evidence quotes) → measurement (Trackman) → interpretation → projection

Confidence is computed HERE from evidence availability — never self-reported
by the model. Conflicts between notes and data are only accepted when
measurement data actually exists. A deterministic markdown renderer keeps the
report text (used by chat context and PDF export) in sync with the JSON.
"""
import json
import re

from backend.services import claude

SYSTEM_STRUCTURED = claude.SYSTEM_SCOUT + """

Mode: STRUCTURED EVALUATION. Return ONLY valid JSON (no markdown fences):
{"name": "string or null", "position": "string or null",
 "grade": "overall: A, A-, B+, B, B-, C+, C, C-, D+, D, or F",
 "summary": "2-3 sentence overview",
 "tools": [
   {"name": "Fastball|Slider|Command|Hit|Power|Speed|Arm|Field|Makeup|... (3-6 tools actually evidenced)",
    "grade": "same scale",
    "evidence": ["EXACT quotes from the scouting notes, verbatim, max 3"],
    "measurement": "specific numbers from MEASUREMENT DATA if present for this tool, else null",
    "interpretation": "one line: what the evidence means now",
    "projection": "one line: forward-looking, hedged appropriately",
    "conflict": "one line ONLY IF notes and measurement data genuinely disagree, else null"}],
 "strengths": ["short strings"], "concerns": ["short strings"],
 "risk": "one-line risk profile",
 "recommendation": "Priority Follow | Follow | Hold | Do Not Follow",
 "recommendation_reason": "one line",
 "what_would_change": ["2-4 specific pieces of information that would change this evaluation"]}

RULES:
- evidence arrays must contain VERBATIM substrings of the notes. Never paraphrase inside evidence.
- measurement must cite actual numbers from MEASUREMENT DATA, or be null. Never invent numbers.
- conflict must be null unless measurement data exists AND genuinely contradicts the notes.
- projection is the most uncertain layer — hedge it honestly."""

GRADE_SCALE = ["A", "A-", "B+", "B", "B-", "C+", "C", "C-", "D+", "D", "F"]
RECOMMENDATIONS = ["Priority Follow", "Follow", "Hold", "Do Not Follow"]


def evaluate(notes: str, rag_context: str = "", trackman_context: str = "") -> dict:
    """Run the structured evaluation. Raises ValueError on unusable model output."""
    blocks = []
    if rag_context:
        blocks.append(f"HISTORICAL REFERENCE (Branch Rickey corpus):\n{rag_context}")
    if trackman_context:
        blocks.append(f"MEASUREMENT DATA (Trackman, this session):\n{trackman_context}")
    blocks.append(f"SCOUTING NOTES:\n{notes}")

    client = claude._client()
    resp = client.messages.create(
        model=claude.MODEL,
        max_tokens=2000,
        system=SYSTEM_STRUCTURED,
        messages=[{"role": "user", "content": "\n\n".join(blocks)}],
    )
    raw = resp.content[0].text.strip()
    raw = re.sub(r"^```(?:json)?\s*", "", raw)
    raw = re.sub(r"\s*```$", "", raw)
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", raw, re.DOTALL)
        if not m:
            raise ValueError("structured evaluation did not return JSON")
        data = json.loads(m.group())

    return finalize(data, notes, had_measurement=bool(trackman_context))


def finalize(data: dict, notes: str, had_measurement: bool) -> dict:
    """Validate model output and compute confidence server-side."""
    tools = []
    for t in data.get("tools", []) or []:
        if not isinstance(t, dict) or not t.get("name"):
            continue
        evidence = [str(q)[:300] for q in (t.get("evidence") or []) if str(q).strip()][:3]
        # Evidence must actually appear in the notes (light fuzz: casefold substring)
        verified = [q for q in evidence if q.casefold()[:60] in notes.casefold()]
        measurement = t.get("measurement") if had_measurement else None
        if isinstance(measurement, str) and not measurement.strip():
            measurement = None
        conflict = t.get("conflict") if (had_measurement and measurement) else None
        if isinstance(conflict, str) and not conflict.strip():
            conflict = None

        tools.append({
            "name": str(t["name"])[:40],
            "grade": t.get("grade") if t.get("grade") in GRADE_SCALE else "C",
            "evidence": verified or evidence,          # keep unverified rather than none
            "evidence_verified": bool(verified),
            "measurement": measurement,
            "interpretation": str(t.get("interpretation", ""))[:300],
            "projection": str(t.get("projection", ""))[:300],
            "conflict": conflict,
            "confidence": _confidence(len(verified or evidence), measurement is not None),
        })

    rec = data.get("recommendation")
    return {
        "name": data.get("name"),
        "position": data.get("position"),
        "grade": data.get("grade") if data.get("grade") in GRADE_SCALE else "C",
        "summary": str(data.get("summary", ""))[:600],
        "tools": tools,
        "strengths": [str(s)[:120] for s in (data.get("strengths") or [])][:6],
        "concerns": [str(s)[:120] for s in (data.get("concerns") or [])][:6],
        "risk": str(data.get("risk", ""))[:300],
        "recommendation": rec if rec in RECOMMENDATIONS else "Follow",
        "recommendation_reason": str(data.get("recommendation_reason", ""))[:300],
        "what_would_change": [str(w)[:200] for w in (data.get("what_would_change") or [])][:4],
        "confidence": _overall_confidence(tools),
        "had_measurement": had_measurement,
    }


def _confidence(n_evidence: int, has_measurement: bool) -> str:
    """Rule-based, from evidence availability — the model never grades its own certainty."""
    if n_evidence >= 2 and has_measurement:
        return "High"
    if n_evidence >= 2 or (n_evidence >= 1 and has_measurement):
        return "Medium"
    return "Low"


def _overall_confidence(tools: list[dict]) -> str:
    if not tools:
        return "Low"
    ranks = {"High": 2, "Medium": 1, "Low": 0}
    avg = sum(ranks[t["confidence"]] for t in tools) / len(tools)
    return "High" if avg >= 1.5 else ("Medium" if avg >= 0.75 else "Low")


def to_markdown(s: dict) -> str:
    """Deterministic markdown of the structured report — feeds chat context + PDF."""
    lines = ["## Player Overview", s["summary"], ""]
    lines.append(f"**Recommendation: {s['recommendation']}** — {s['recommendation_reason']}")
    lines.append(f"**Overall confidence:** {s['confidence']}")
    lines.append("")
    lines.append("## Tools")
    for t in s["tools"]:
        lines.append(f"### {t['name']} — {t['grade']} (confidence: {t['confidence']})")
        for q in t["evidence"]:
            lines.append(f'- Evidence: "{q}"')
        if t["measurement"]:
            lines.append(f"- Measurement: {t['measurement']}")
        lines.append(f"- Interpretation: {t['interpretation']}")
        if t["projection"]:
            lines.append(f"- Projection: {t['projection']}")
        if t["conflict"]:
            lines.append(f"- ⚠️ Data conflict: {t['conflict']}")
        lines.append("")
    if s["strengths"]:
        lines.append("## Key Strengths")
        lines += [f"- {x}" for x in s["strengths"]]
        lines.append("")
    if s["concerns"]:
        lines.append("## Areas of Concern")
        lines += [f"- {x}" for x in s["concerns"]]
        lines.append("")
    lines.append("## Risk")
    lines.append(s["risk"])
    if s["what_would_change"]:
        lines.append("")
        lines.append("## What Would Change This Evaluation")
        lines += [f"- {x}" for x in s["what_would_change"]]
    lines.append("")
    lines.append(f"## Recommendation & Grade\n{s['grade']} — {s['recommendation']}")
    return "\n".join(lines)
