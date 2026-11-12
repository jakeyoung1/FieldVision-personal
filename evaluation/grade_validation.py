"""Compare FieldVision's AI grades with a coach's grades on the same notes.

    .venv/bin/python -m evaluation.grade_validation generate   # AI grades (calls the API)
    .venv/bin/python -m evaluation.grade_validation sheet      # blank coach sheet, no AI grades on it
    .venv/bin/python -m evaluation.grade_validation score      # compare, write results

The coach grades blind: the sheet carries the notes and nothing the model said.

Metrics (only over rows a coach has actually filled in):
  MAE           mean absolute gap on the 20-80 scale
  bias          mean signed gap (AI minus coach); positive = the model grades high
  Spearman rho  do the two graders rank the players the same way? Unaffected by
                the letter-to-20-80 conversion below, so it is the honest check
                if that conversion is off
  agreement     share of players where both graders give the same Take / Follow / Hold
  Cohen's kappa that agreement corrected for chance

Placeholder rows (status PLACEHOLDER) are never scored. Until a coach fills the
sheet, `score` reports that no comparison exists rather than a number.
"""
import argparse
import csv
import json
import math
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).parent / "grades"
PLAYERS = HERE / "players.json"
AI_GRADES = HERE / "ai_grades.json"
AI_REPEAT = HERE / "ai_grades_repeat.json"
COACH_GRADES = HERE / "coach_grades.csv"
RESULTS = Path(__file__).parent / "results"

# The app grades on letters. Scouts grade on 20-80 (50 = average regular).
# This conversion is an assumption, not a standard: it puts a C at 40 and
# spaces each step 5 points apart. MAE and bias depend on it; rank correlation
# and decision agreement do not.
LETTER_TO_2080 = {"A": 70, "A-": 65, "B+": 60, "B": 55, "B-": 50, "C+": 45,
                  "C": 40, "C-": 35, "D+": 30, "D": 25, "F": 20}

# The app's four recommendations folded onto the three decisions coaches use.
DECISION = {"Priority Follow": "Take", "Follow": "Follow", "Hold": "Hold", "Do Not Follow": "Hold"}
DECISIONS = ("Take", "Follow", "Hold")

COACH_COLUMNS = ["id", "status", "notes", "coach_grade", "coach_decision", "grader", "graded_on"]


# ── Conversions ──────────────────────────────────────────────────────────────
def to_2080(grade) -> float | None:
    """A 20-80 number, or a letter converted with LETTER_TO_2080."""
    if grade is None:
        return None
    text = str(grade).strip()
    if not text:
        return None
    if text.upper() in LETTER_TO_2080:
        return float(LETTER_TO_2080[text.upper()])
    value = float(text)
    if not 20 <= value <= 80:
        raise ValueError(f"grade {value} is outside 20-80")
    return value


def to_decision(value: str) -> str | None:
    text = (value or "").strip()
    if not text:
        return None
    if text in DECISION:
        return DECISION[text]
    title = text.title()
    if title in DECISIONS:
        return title
    raise ValueError(f"unknown decision {value!r}; use Take, Follow, or Hold")


# ── Statistics ───────────────────────────────────────────────────────────────
def _ranks(xs: list[float]) -> list[float]:
    order = sorted(range(len(xs)), key=lambda i: xs[i])
    ranks = [0.0] * len(xs)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and xs[order[j + 1]] == xs[order[i]]:
            j += 1
        for k in range(i, j + 1):
            ranks[order[k]] = (i + j) / 2 + 1
        i = j + 1
    return ranks


def spearman(a: list[float], b: list[float]) -> float | None:
    if len(a) < 3:
        return None
    ra, rb = _ranks(a), _ranks(b)
    ma, mb = sum(ra) / len(ra), sum(rb) / len(rb)
    cov = sum((x - ma) * (y - mb) for x, y in zip(ra, rb))
    sa = math.sqrt(sum((x - ma) ** 2 for x in ra))
    sb = math.sqrt(sum((y - mb) ** 2 for y in rb))
    return cov / (sa * sb) if sa and sb else None


def cohens_kappa(a: list[str], b: list[str]) -> float | None:
    n = len(a)
    if not n:
        return None
    observed = sum(x == y for x, y in zip(a, b)) / n
    expected = sum((a.count(c) / n) * (b.count(c) / n) for c in DECISIONS)
    return (observed - expected) / (1 - expected) if expected < 1 else None


def compare(pairs: list[dict]) -> dict:
    """pairs: [{ai_2080, coach_2080, ai_decision, coach_decision}], real rows only."""
    graded = [p for p in pairs if p["coach_2080"] is not None]
    decided = [p for p in pairs if p["coach_decision"] is not None]
    gaps = [p["ai_2080"] - p["coach_2080"] for p in graded]
    rho = spearman([p["ai_2080"] for p in graded], [p["coach_2080"] for p in graded])
    agree = [p["ai_decision"] == p["coach_decision"] for p in decided]
    kappa = cohens_kappa([p["ai_decision"] for p in decided], [p["coach_decision"] for p in decided])
    return {
        "n_grades": len(graded),
        "mae_2080": round(sum(abs(g) for g in gaps) / len(gaps), 2) if gaps else None,
        "bias_2080": round(sum(gaps) / len(gaps), 2) if gaps else None,
        "spearman_rho": round(rho, 3) if rho is not None else None,
        "n_decisions": len(decided),
        "decision_agreement": round(sum(agree) / len(agree), 3) if agree else None,
        "cohens_kappa": round(kappa, 3) if kappa is not None else None,
    }


# ── Files ────────────────────────────────────────────────────────────────────
def load_players() -> list[dict]:
    return json.loads(PLAYERS.read_text())["players"]


def load_ai(path: Path = AI_GRADES) -> dict[str, dict]:
    if not path.exists():
        return {}
    return {g["id"]: g for g in json.loads(path.read_text())["grades"]}


def load_coach() -> list[dict]:
    if not COACH_GRADES.exists():
        return []
    with COACH_GRADES.open(newline="") as f:
        return list(csv.DictReader(f))


def real_coach_rows(rows: list[dict]) -> list[dict]:
    """Rows a coach has filled in. Anything still marked PLACEHOLDER is skipped."""
    return [r for r in rows
            if r.get("status", "").strip().lower() == "coach"
            and (r.get("coach_grade", "").strip() or r.get("coach_decision", "").strip())]


# ── Commands ─────────────────────────────────────────────────────────────────
def generate(workers: int = 4, out: Path = AI_GRADES) -> None:
    """Grade every player with the production Evidence Chain. Costs API calls."""
    try:
        from dotenv import load_dotenv
        load_dotenv(Path(__file__).parent.parent / ".env")
    except ImportError:
        pass
    from backend.services import claude, rag, scout_report

    def one(p):
        context = rag.context_block(p["notes"][:500])
        s = scout_report.evaluate(p["notes"], context, "")
        return {"id": p["id"], "grade": s["grade"], "recommendation": s["recommendation"],
                "confidence": s["confidence"], "rickey_context_used": bool(context),
                "tools": {t["name"]: t["grade"] for t in s["tools"]}}

    players = load_players()
    with ThreadPoolExecutor(max_workers=workers) as pool:
        grades = list(pool.map(one, players))
    out.write_text(json.dumps({
        "_meta": {"generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                  "model": claude.MODEL, "pipeline": "backend.services.scout_report.evaluate",
                  "trackman": "none", "note": "Real model output. Regenerating will change some grades."},
        "grades": grades}, indent=1) + "\n")
    print(f"wrote {len(grades)} AI grades to {out}")


def retest(ai: dict[str, dict], repeat: dict[str, dict]) -> dict | None:
    """The model against itself: same notes, same pipeline, a second run.

    However closely the model agrees with a coach, it cannot be more
    consistent with a coach than it is with itself, so this bounds what the
    coach comparison can show.
    """
    ids = sorted(set(ai) & set(repeat))
    if not ids:
        return None
    pairs = [{"ai_2080": to_2080(ai[i]["grade"]), "coach_2080": to_2080(repeat[i]["grade"]),
              "ai_decision": DECISION[ai[i]["recommendation"]],
              "coach_decision": DECISION[repeat[i]["recommendation"]]} for i in ids]
    c = compare(pairs)
    c["same_letter"] = round(sum(ai[i]["grade"] == repeat[i]["grade"] for i in ids) / len(ids), 3)
    c["within_one_step"] = round(sum(abs(to_2080(ai[i]["grade"]) - to_2080(repeat[i]["grade"])) <= 5
                                     for i in ids) / len(ids), 3)
    return c


def sheet() -> None:
    """Write the blind coach sheet: notes only, every grade cell empty."""
    with COACH_GRADES.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=COACH_COLUMNS)
        w.writeheader()
        for p in load_players():
            w.writerow({"id": p["id"], "status": "PLACEHOLDER", "notes": p["notes"],
                        "coach_grade": "", "coach_decision": "", "grader": "", "graded_on": ""})
    print(f"wrote blank sheet for {len(load_players())} players to {COACH_GRADES}")


def score() -> dict:
    ai = load_ai()
    rows = load_coach()
    real = real_coach_rows(rows)
    pairs = []
    for r in real:
        g = ai.get(r["id"])
        if g is None:
            continue
        pairs.append({"id": r["id"],
                      "ai_2080": to_2080(g["grade"]), "coach_2080": to_2080(r["coach_grade"]),
                      "ai_decision": DECISION[g["recommendation"]],
                      "coach_decision": to_decision(r["coach_decision"])})
    result = {
        "players": len(load_players()),
        "ai_grades": len(ai),
        "coach_rows": len(rows),
        "placeholder_rows": sum(r.get("status", "").strip().upper() == "PLACEHOLDER" for r in rows),
        "coach_rows_filled": len(real),
        "comparison": compare(pairs) if pairs else None,
        "ai_retest": retest(ai, load_ai(AI_REPEAT)),
        "ai_distribution": {
            "grades": {k: sum(g["grade"] == k for g in ai.values()) for k in LETTER_TO_2080},
            "decisions": {d: sum(DECISION[g["recommendation"]] == d for g in ai.values()) for d in DECISIONS},
        },
    }
    RESULTS.mkdir(exist_ok=True)
    (RESULTS / "grade_validation.json").write_text(json.dumps(result, indent=2) + "\n")
    (RESULTS / "grade_validation.md").write_text(to_markdown(result) + "\n")
    print(to_markdown(result))
    return result


def to_markdown(r: dict) -> str:
    lines = ["# Grade validation: AI vs coach", "",
             "Generated by `python -m evaluation.grade_validation score`. Do not edit by hand.", ""]
    c = r["comparison"]
    if c is None:
        lines += [f"**No comparison yet.** {r['placeholder_rows']} of {r['coach_rows']} coach rows are "
                  "PLACEHOLDER (empty). No coach grades have been collected, so there is no MAE, "
                  "agreement, or correlation to report.", ""]
    else:
        lines += [f"Coach rows filled: {r['coach_rows_filled']} of {r['players']}.", "",
                  "| Metric | Value |", "|---|---|",
                  f"| MAE, 20-80 scale (n={c['n_grades']}) | {c['mae_2080']} |",
                  f"| Bias, AI minus coach | {c['bias_2080']:+} |" if c["bias_2080"] is not None else "| Bias | n/a |",
                  f"| Spearman rho | {c['spearman_rho']} |",
                  f"| Take/Follow/Hold agreement (n={c['n_decisions']}) | {c['decision_agreement']} |",
                  f"| Cohen's kappa | {c['cohens_kappa']} |", ""]
    t = r.get("ai_retest")
    if t:
        lines += ["## AI against itself (test-retest)", "",
                  "Same 20 notes, same pipeline, run twice. No coach involved.", "",
                  "| Metric | Value |", "|---|---|",
                  f"| Same letter grade | {t['same_letter']:.0%} |",
                  f"| Within one step (5 points on 20-80) | {t['within_one_step']:.0%} |",
                  f"| MAE between runs, 20-80 | {t['mae_2080']} |",
                  f"| Spearman rho between runs | {t['spearman_rho']} |",
                  f"| Same Take/Follow/Hold | {t['decision_agreement']:.0%} |",
                  f"| Cohen's kappa | {t['cohens_kappa']} |", "",
                  "The model cannot agree with a coach more consistently than it agrees with itself.", ""]
    d = r["ai_distribution"]
    lines += [f"## AI side ({r['ai_grades']} players, real model output)", "",
              "Grades: " + ", ".join(f"{k} {v}" for k, v in d["grades"].items() if v),
              "", "Decisions: " + ", ".join(f"{k} {v}" for k, v in d["decisions"].items()), "",
              "Letter grades convert to 20-80 as A=70 down to F=20 in steps of 5 (C=40). "
              "That conversion is an assumption; Spearman rho and decision agreement do not depend on it."]
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("command", choices=["generate", "sheet", "score"])
    parser.add_argument("--repeat", action="store_true",
                        help="with generate: write a second run for the test-retest check")
    args = parser.parse_args()
    if args.command == "generate":
        generate(out=AI_REPEAT if args.repeat else AI_GRADES)
    else:
        {"sheet": sheet, "score": score}[args.command]()


if __name__ == "__main__":
    main()
