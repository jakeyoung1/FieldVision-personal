"""The overall grade on the published pro scale: a calibration, added beside the letter.

The model's overall letter is left exactly as written. This module only adds a
second number next to it.

Why it exists. On the pro benchmark the overall letter, converted to 20-80, ran
high against one outlet's published overall grades. The cause was a difference
in meaning: the model sets its overall at about the average of its own tool
grades, while the published overall sits below the published tool grades, by
more for pitchers than for position players.

THE RULE IS FROZEN. Convert the letter with the same table the benchmark scorer
uses, then lower it two steps for a pitcher and one step for a position player
(one step is 5 points), and keep the result on the 20-80 scale. The step sizes
were chosen on the benchmark's development split and checked once on its test
split, which passed (agent-relay #179). That test split is now spent for this
rule: changing a step size here would be tuning on it. tests/test_pro_scale.py
pins the rule and the numbers it was validated with.

What it is not. It is not a second evaluation and it does not rank players any
better than the letter does. It has not been checked against a coach's grades
or against players below the college-draft level.
"""
from __future__ import annotations

from backend.services import rag

STEP = 5
SCALE_MIN, SCALE_MAX = 20, 80
# Same table as evaluation/pro_benchmark/score_pro_benchmark.py and
# evaluation/grade_validation.py. A test keeps the three equal.
LETTER_TO_2080 = {"A": 70, "A-": 65, "B+": 60, "B": 55, "B-": 50, "C+": 45,
                  "C": 40, "C-": 35, "D+": 30, "D": 25, "F": 20}
STEPS_DOWN = {rag.ROLE_PITCHER: 2, rag.ROLE_HITTER: 1}

LABEL = "Published pro scale"
KIND = "calibration"
_STEP_WORDS = {1: "one step", 2: "two steps"}
BASIS = (
    "A calibration of the overall letter, not a second evaluation. The letter is converted to the "
    f"{SCALE_MIN} to {SCALE_MAX} scouting scale and lowered {_STEP_WORDS[STEPS_DOWN[rag.ROLE_PITCHER]]} for a pitcher "
    f"or {_STEP_WORDS[STEPS_DOWN[rag.ROLE_HITTER]]} for a position player. The step sizes were set on one group of "
    "college draftees and checked once on a second group, against one outlet's published grades. "
    "It has not been checked against a coach's grades."
)
NO_ROLE = "Not shown: the notes do not make clear whether this is a pitcher or a position player."


def clamp(value: float) -> int:
    return int(min(SCALE_MAX, max(SCALE_MIN, value)))


def step_down(value: float, role: str) -> int | None:
    """A 20-80 overall lowered by the role's steps, kept on the scale. None for any other role."""
    steps = STEPS_DOWN.get(role)
    if steps is None:
        return None
    return clamp(value - steps * STEP)


def role_for(notes: str, position: str | None = None) -> tuple[str | None, str | None]:
    """(role, where it came from). The notes decide; the model's position line is the fallback.

    A two-way player, or notes that name neither role, get no role and so no value.
    """
    role = rag.infer_role(notes or "")
    if role in STEPS_DOWN:
        return role, "notes"
    if position:
        role = rag.infer_role(str(position)[:60])
        if role in STEPS_DOWN:
            return role, "position"
    return None, None


def calibrate(grade: str, notes: str, position: str | None = None) -> dict:
    """The field stored beside the overall letter. `value` is None when no role is clear."""
    role, source = role_for(notes, position)
    from_value = LETTER_TO_2080.get(grade)
    value = step_down(from_value, role) if (from_value is not None and role) else None
    return {
        "label": LABEL,
        "kind": KIND,
        "value": value,
        "scale": f"{SCALE_MIN} to {SCALE_MAX}",
        "from_grade": grade,
        "from_value": from_value,
        "role": role,
        "role_source": source,
        "steps_down": STEPS_DOWN.get(role),
        "basis": BASIS if value is not None else NO_ROLE,
    }


def markdown_line(field: dict | None) -> str | None:
    """One line for the text report, or None when there is no value to show."""
    if not field or field.get("value") is None:
        return None
    return (f"{field['label']} ({field['kind']} of the overall letter {field['from_grade']}): "
            f"{field['value']} on the {field['scale']} scale")
