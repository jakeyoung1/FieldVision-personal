"""Score the published-pro-scale calibration of the overall grade.

    python -m evaluation.pro_benchmark.score_pro_scale            # rewrite the results file
    python -m evaluation.pro_benchmark.score_pro_scale --check    # fail if the file is stale

Reads the committed grades (fieldvision_grades_dev.json, fieldvision_grades_test.json)
and the public benchmark. Makes no model call. Writes benchmark_pro_scale_results.json,
which /methods renders.

The calibration is backend/services/pro_scale.py: the model's overall letter converted
to 20-80, lowered two steps for a pitcher and one for a position player. This script
applies that shipped function; it has no rule of its own. Role is the benchmark's
`role` column, as in the validation.

THE RULE IS FROZEN. Its step sizes were chosen on the development split and checked
once on the test split (2026-11-14, agent-relay #179). Rerunning this script repeats
that same computation and gives the same numbers; it is not a new look at the test
split, and nothing here may be used to adjust the rule.
"""
from __future__ import annotations

import argparse
import csv
import json
import random
import sys
from pathlib import Path

from backend.services import pro_scale
from evaluation.pro_benchmark import score_pro_benchmark as scorer

HERE = Path(__file__).parent
PUBLIC = HERE / "pro_benchmark.public.csv"
OUT = HERE / "benchmark_pro_scale_results.json"
CONSTANT = 45.0          # the single grade for everyone, chosen on dev as its median published overall
CHECKED_ON = "2026-11-14"
ROLE_NAMES = {"pitcher": "pitcher", "hitter": "position player"}

mean = lambda xs: sum(xs) / len(xs)  # noqa: E731


def _r(value):
    return round(value, 4) if isinstance(value, float) else value


def _row(pred: dict, pub: dict, ids: list[str]) -> dict:
    point, ci = scorer.bootstrap({i: [(pred[i], pub[i])] for i in ids})
    return {**{k: _r(v) for k, v in point.items()}, "ci95": {k: [_r(a), _r(b)] for k, (a, b) in ci.items()}}


def _boot(fn, ids: list[str]) -> list[float] | None:
    rng = random.Random(scorer.SEED)
    pool = sorted(ids)
    values = []
    for _ in range(scorer.DRAWS):
        v = fn([pool[rng.randrange(len(pool))] for _ in pool])
        if v is not None:
            values.append(v)
    if not values:
        return None
    values.sort()
    return [_r(values[int(0.025 * len(values))]), _r(values[min(len(values) - 1, int(0.975 * len(values)))])]


def score_split(split: str) -> dict:
    with PUBLIC.open(newline="", encoding="utf-8-sig") as f:
        rows = {r["id"]: r for r in csv.DictReader(f) if r["split"] == split}
    grades = json.loads((HERE / f"fieldvision_grades_{split}.json").read_text())["grades"]
    ids = sorted(rows)
    if {g["id"] for g in grades} != set(ids):
        raise ValueError(f"grades file and benchmark disagree on the {split} players")
    role = {i: rows[i]["role"] for i in ids}
    if not set(role.values()) <= set(pro_scale.STEPS_DOWN):
        raise ValueError(f"a {split} player has a role the rule does not cover")
    pub = {i: float(rows[i]["pub_overall"]) for i in ids}
    before = {g["id"]: float(pro_scale.LETTER_TO_2080[g["grade"]]) for g in grades}
    after = {i: float(pro_scale.step_down(before[i], role[i])) for i in ids}

    def d_mae(p):
        return mean([abs(after[i] - pub[i]) - abs(before[i] - pub[i]) for i in p])

    def d_within(p):
        return mean([(abs(after[i] - pub[i]) <= scorer.STEP) - (abs(before[i] - pub[i]) <= scorer.STEP) for i in p])

    def d_spearman(p):
        a = scorer.spearman([after[i] for i in p], [pub[i] for i in p])
        b = scorer.spearman([before[i] for i in p], [pub[i] for i in p])
        return None if a is None or b is None else a - b

    by_role = {}
    for r in pro_scale.STEPS_DOWN:
        members = [i for i in ids if role[i] == r]
        by_role[r] = {"name": ROLE_NAMES[r], "players": len(members), "steps_down": pro_scale.STEPS_DOWN[r],
                      "before": _row(before, pub, members), "after": _row(after, pub, members)}
    result = {
        "players": len(ids),
        "before": _row(before, pub, ids),
        "after": _row(after, pub, ids),
        "paired_change": {"mae": _r(d_mae(ids)), "within_step": _r(d_within(ids)), "spearman": _r(d_spearman(ids)),
                          "ci95": {"mae": _boot(d_mae, ids), "within_step": _boot(d_within, ids),
                                   "spearman": _boot(d_spearman, ids)}},
        "by_role": by_role,
        "same_grade_for_everyone": {"value": CONSTANT, **_row({i: CONSTANT for i in ids}, pub, ids)},
        "published_overall_values": sorted(set(pub.values())),
    }
    result["conditions"] = _conditions(result)
    return result


def _conditions(r: dict) -> list[dict]:
    """The six conditions set before the test players were scored (agent-relay #179)."""
    after, change = r["after"], r["paired_change"]
    half_step, step = scorer.STEP / 2, scorer.STEP
    role_bias = {v["name"]: v["after"]["bias"] for v in r["by_role"].values()}
    return [
        {"condition": "overall bias within half a step", "held": abs(after["bias"]) <= half_step},
        {"condition": "bias interval within one step of zero",
         "held": -step <= after["ci95"]["bias"][0] and after["ci95"]["bias"][1] <= step},
        {"condition": "average gap smaller, interval on the change below zero", "held": change["ci95"]["mae"][1] < 0},
        {"condition": "more grades within one step, interval on the change above zero",
         "held": change["ci95"]["within_step"][0] > 0},
        {"condition": "each role's bias within one step", "held": all(abs(v) <= step for v in role_bias.values())},
        {"condition": "rank agreement not worse", "held": not change["ci95"]["spearman"][1] < 0},
    ]


def diagnosis() -> dict:
    """Why two step sizes: where the overall sits against the tool grades, on each side. Dev only."""
    with PUBLIC.open(newline="", encoding="utf-8-sig") as f:
        rows = {r["id"]: r for r in csv.DictReader(f) if r["split"] == "dev"}
    grades = {g["id"]: g for g in json.loads((HERE / "fieldvision_grades_dev.json").read_text())["grades"]}
    ids = sorted(rows)
    tool_columns = [f"pub_{t}" for t in scorer.TOOLS]
    pub_gap = {i: float(rows[i]["pub_overall"]) - mean([float(rows[i][c]) for c in tool_columns if rows[i][c].strip()])
               for i in ids}
    own_gap = {i: pro_scale.LETTER_TO_2080[grades[i]["grade"]]
               - mean([pro_scale.LETTER_TO_2080[t["grade"]] for t in grades[i]["tools"]]) for i in ids}
    out = {"split": "dev", "players": len(ids)}
    for key, gaps in (("published_overall_minus_published_tools", pub_gap), ("fieldvision_overall_minus_its_tools", own_gap)):
        out[key] = {}
        for name, members in (("all", ids), *((r, [i for i in ids if rows[i]["role"] == r]) for r in pro_scale.STEPS_DOWN)):
            fn = lambda p, g=gaps: mean([g[i] for i in p])  # noqa: E731
            out[key][name] = {"mean": _r(fn(members)), "ci95": _boot(fn, members)}
    return out


def build() -> dict:
    return {
        "_meta": {
            "what": "The overall grade calibrated to the published pro scale, scored against published overall grades.",
            "rule": {"steps_down": dict(pro_scale.STEPS_DOWN), "step_points": pro_scale.STEP,
                     "lowest": pro_scale.SCALE_MIN, "highest": pro_scale.SCALE_MAX,
                     "applies_to": "the model's overall letter; tool grades and the letter itself are not changed",
                     "role_from": "the benchmark's role column"},
            "chosen_on": "dev", "checked_on": "test", "checked_once": CHECKED_ON,
            "frozen": "The test split was used once to check this rule. Nothing here may be used to adjust it.",
            "inputs": ["pro_benchmark.public.csv", "fieldvision_grades_dev.json", "fieldvision_grades_test.json"],
            "model_calls": 0, "draws": scorer.DRAWS, "seed": scorer.SEED,
        },
        "dev": score_split("dev"),
        "test": score_split("test"),
        "why_two_step_sizes": diagnosis(),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--check", action="store_true", help="fail if the committed file differs")
    args = ap.parse_args()
    text = json.dumps(build(), indent=2) + "\n"
    if args.check:
        same = OUT.exists() and OUT.read_text() == text
        print("up to date" if same else f"{OUT.name} is stale; rerun without --check")
        return 0 if same else 1
    OUT.write_text(text)
    t = json.loads(text)["test"]
    print(f"wrote {OUT.name}: test bias {t['before']['bias']} -> {t['after']['bias']}, "
          f"conditions held {sum(c['held'] for c in t['conditions'])} of {len(t['conditions'])}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
