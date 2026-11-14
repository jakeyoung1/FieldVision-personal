"""Score FieldVision grades against published MLB Pipeline 20-80 grades.

    python score_pro_benchmark.py --outputs fieldvision_outputs.csv [--split test]
    python score_pro_benchmark.py --from-ai-json ai_grades.json --split test

Inputs
  pro_benchmark.csv          the human benchmark (published grades, one row per player)
  FieldVision outputs        either
    * a CSV shaped like fieldvision_outputs_TEMPLATE.csv (one column per tool,
      letters A..F or 20-80 numbers), or
    * a JSON in the repo's evaluation/grades/ai_grades.json format
      ({"grades": [{"id", "grade", "tools": {"Fastball": "B+", ...}}]}),
      where "id" is the benchmark id (pb001...).

Rows with status PLACEHOLDER (or no grades at all) are never scored. If nothing
is scorable the script says so instead of printing a number.

Metrics per tool, for all tools pooled, and for the overall grade:
  n            pairs scored
  MAE          mean |FieldVision - published| on 20-80
  bias         mean (FieldVision - published); positive = FieldVision grades high
  within_step  share of pairs with |gap| <= 5 (one grade step on the 20-80 scale)
  spearman     rank correlation (unaffected by the letter->20-80 conversion)
Each with a percentile bootstrap 95% CI, resampling PLAYERS (not pairs) with
replacement, 2,000 draws, seed 20261112.

Letter -> 20-80 conversion: copied from evaluation/grade_validation.py
(LETTER_TO_2080, C=40, 5 points per step, A=70 cap). It is an assumption, not a
standard. FieldVision letters cannot express 75 or 80; published grades of 75+
will always show a gap. Spearman does not depend on the conversion.
"""
import argparse
import csv
import json
import math
import random
import re
from pathlib import Path

HERE = Path(__file__).parent
BENCH = HERE / "pro_benchmark.csv"
if not BENCH.exists():  # full file is gitignored; grades-only copy is committed
    BENCH = HERE / "pro_benchmark.public.csv"
SEED = 20261112
DRAWS = 2000
STEP = 5

# Same table as evaluation/grade_validation.py at main 4607765.
LETTER_TO_2080 = {"A": 70, "A-": 65, "B+": 60, "B": 55, "B-": 50, "C+": 45,
                  "C": 40, "C-": 35, "D+": 30, "D": 25, "F": 20}

TOOLS = ["hit", "power", "run", "arm", "field", "fastball", "slider", "curveball",
         "changeup", "cutter", "splitter", "control"]
ALL_KEYS = TOOLS + ["overall"]

# FieldVision tool names are free text ("Fastball|Slider|Command|Hit|Power|Speed|
# Arm|Field|Makeup|..."). Map the common ones onto benchmark tools. Anything not
# listed (Makeup, Pitchability, Durability, ...) has no published counterpart and
# is ignored. "Breaking Ball" is resolved per player below.
NAME_MAP = {
    "hit": "hit", "hitting": "hit", "hit tool": "hit", "contact": "hit", "bat": "hit",
    "power": "power", "game power": "power", "raw power": "power",
    "run": "run", "running": "run", "speed": "run", "run tool": "run",
    "arm": "arm", "arm strength": "arm", "throwing": "arm",
    "field": "field", "fielding": "field", "defense": "field", "glove": "field",
    "fastball": "fastball", "four-seam fastball": "fastball", "sinker": "fastball",
    "slider": "slider", "sweeper": "slider",
    "curveball": "curveball", "curve": "curveball",
    "changeup": "changeup", "change": "changeup",
    "cutter": "cutter", "splitter": "splitter", "split-finger": "splitter",
    "control": "control", "command": "control", "command/control": "control",
}
BREAKING = {"breaking ball", "breaking pitch", "breaker"}


# ── conversions ──────────────────────────────────────────────────────────────
def to_2080(value):
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    if text.upper() in LETTER_TO_2080:
        return float(LETTER_TO_2080[text.upper()])
    v = float(text)
    if not 20 <= v <= 80:
        raise ValueError(f"grade {v} is outside 20-80")
    return v


def map_tool(name: str, published: dict) -> str | None:
    key = re.sub(r"\s+", " ", name.strip().lower())
    if key in NAME_MAP:
        return NAME_MAP[key]
    if key in BREAKING:
        have = [t for t in ("slider", "curveball") if published.get(t) is not None]
        return have[0] if len(have) == 1 else None   # ambiguous when both are graded
    return None


# ── loading ──────────────────────────────────────────────────────────────────
def load_bench(path: Path = BENCH) -> dict[str, dict]:
    with path.open(newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    out = {}
    for r in rows:
        pub = {k: (float(r[f"pub_{k}"]) if r.get(f"pub_{k}", "").strip() else None) for k in ALL_KEYS}
        out[r["id"]] = {"id": r["id"], "split": r["split"], "role": r["role"], "published": pub}
    return out


def load_outputs_csv(path: Path, bench: dict) -> dict[str, dict]:
    with path.open(newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    out = {}
    for r in rows:
        if r.get("status", "").strip().upper() == "PLACEHOLDER":
            continue
        pid = r["id"].strip()
        if pid not in bench:
            raise ValueError(f"unknown benchmark id {pid!r}")
        grades = {k: to_2080(r.get(k)) for k in ALL_KEYS}
        if any(v is not None for v in grades.values()):
            out[pid] = grades
    return out


def load_outputs_ai_json(path: Path, bench: dict) -> dict[str, dict]:
    data = json.loads(path.read_text())
    out = {}
    for g in data.get("grades", []):
        pid = g["id"]
        if pid not in bench:
            raise ValueError(f"unknown benchmark id {pid!r}")
        pub = bench[pid]["published"]
        grades = {k: None for k in ALL_KEYS}
        grades["overall"] = to_2080(g.get("grade"))
        tools = g.get("tools") or {}
        if isinstance(tools, list):  # full structured output: [{"name","grade",...}]
            tools = {t.get("name", ""): t.get("grade") for t in tools}
        for name, letter in tools.items():
            key = map_tool(name, pub)
            if key and grades.get(key) is None:   # first mention wins (e.g. Command vs Control)
                grades[key] = to_2080(letter)
        if any(v is not None for v in grades.values()):
            out[pid] = grades
    return out


# ── statistics ───────────────────────────────────────────────────────────────
def _ranks(xs):
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


def spearman(a, b):
    if len(a) < 3:
        return None
    ra, rb = _ranks(a), _ranks(b)
    ma, mb = sum(ra) / len(ra), sum(rb) / len(rb)
    cov = sum((x - ma) * (y - mb) for x, y in zip(ra, rb))
    sa = math.sqrt(sum((x - ma) ** 2 for x in ra))
    sb = math.sqrt(sum((y - mb) ** 2 for y in rb))
    return cov / (sa * sb) if sa and sb else None


def metrics(pairs):
    """pairs: list of (fieldvision, published)."""
    if not pairs:
        return {"n": 0, "mae": None, "bias": None, "within_step": None, "spearman": None}
    gaps = [a - b for a, b in pairs]
    return {
        "n": len(pairs),
        "mae": sum(abs(g) for g in gaps) / len(gaps),
        "bias": sum(gaps) / len(gaps),
        "within_step": sum(abs(g) <= STEP for g in gaps) / len(gaps),
        "spearman": spearman([a for a, _ in pairs], [b for _, b in pairs]),
    }


def pairs_by_player(outputs, bench, key):
    """{player_id: [(fv, pub), ...]} for one tool, 'overall', or '_pooled' (all tools)."""
    out = {}
    for pid, fv in outputs.items():
        pub = bench[pid]["published"]
        keys = TOOLS if key == "_pooled" else [key]
        ps = [(fv[k], pub[k]) for k in keys if fv.get(k) is not None and pub.get(k) is not None]
        if ps:
            out[pid] = ps
    return out


def bootstrap(by_player, draws=DRAWS, seed=SEED):
    ids = sorted(by_player)
    point = metrics([p for i in ids for p in by_player[i]])
    if len(ids) < 2:
        return point, {}
    rng = random.Random(seed)
    samples = {m: [] for m in ("mae", "bias", "within_step", "spearman")}
    for _ in range(draws):
        pick = [ids[rng.randrange(len(ids))] for _ in ids]
        m = metrics([p for i in pick for p in by_player[i]])
        for k in samples:
            if m[k] is not None:
                samples[k].append(m[k])
    ci = {}
    for k, xs in samples.items():
        if len(xs) >= 0.9 * draws:
            xs.sort()
            ci[k] = (xs[int(0.025 * len(xs))], xs[min(len(xs) - 1, int(0.975 * len(xs)))])
    return point, ci


def score(outputs, bench, split=None, draws=DRAWS, seed=SEED):
    if split:
        outputs = {k: v for k, v in outputs.items() if bench[k]["split"] == split}
    result = {"split": split or "all", "players_scored": len(outputs), "draws": draws,
              "seed": seed, "step_points": STEP, "by_tool": {}}
    for key in ALL_KEYS + ["_pooled"]:
        point, ci = bootstrap(pairs_by_player(outputs, bench, key), draws, seed)
        result["by_tool"][key] = {**{k: (round(v, 4) if isinstance(v, float) else v) for k, v in point.items()},
                                  "ci95": {k: [round(a, 4), round(b, 4)] for k, (a, b) in ci.items()}}
    return result


def to_markdown(r):
    lines = ["# FieldVision vs published MLB Pipeline grades", "",
             f"Split: {r['split']}. Players scored: {r['players_scored']}. "
             f"Bootstrap: {r['draws']} player resamples, seed {r['seed']}. One step = {r['step_points']} points.", ""]
    if not r["players_scored"]:
        lines.append("**No FieldVision outputs to score yet.** Fill fieldvision_outputs.csv (or pass --from-ai-json).")
        return "\n".join(lines)
    lines += ["| Tool | n | MAE [95% CI] | Bias | Within one step [95% CI] | Spearman [95% CI] |",
              "|---|---|---|---|---|---|"]

    def fmt(v, ci, pct=False):
        if v is None:
            return "n/a"
        f = (lambda x: f"{x:.0%}") if pct else (lambda x: f"{x:.2f}")
        return f(v) + (f" [{f(ci[0])}, {f(ci[1])}]" if ci else "")
    for key, m in r["by_tool"].items():
        if not m["n"]:
            continue
        name = "ALL TOOLS (pooled)" if key == "_pooled" else key
        c = m["ci95"]
        lines.append(f"| {name} | {m['n']} | {fmt(m['mae'], c.get('mae'))} | {fmt(m['bias'], c.get('bias'))} | "
                     f"{fmt(m['within_step'], c.get('within_step'), True)} | {fmt(m['spearman'], c.get('spearman'))} |")
    lines += ["", "Letters convert A=70 ... C=40 ... F=20 (assumption from evaluation/grade_validation.py). "
              "Spearman is unaffected by that conversion."]
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--bench", default=str(BENCH))
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--outputs", help="CSV shaped like fieldvision_outputs_TEMPLATE.csv")
    src.add_argument("--from-ai-json", help="JSON in evaluation/grades/ai_grades.json format")
    ap.add_argument("--split", choices=["dev", "test"])
    ap.add_argument("--write", help="write JSON + markdown with this path prefix")
    a = ap.parse_args()
    bench = load_bench(Path(a.bench))
    outputs = (load_outputs_csv(Path(a.outputs), bench) if a.outputs
               else load_outputs_ai_json(Path(a.from_ai_json), bench))
    r = score(outputs, bench, a.split)
    md = to_markdown(r)
    print(md)
    if a.write:
        Path(a.write + ".json").write_text(json.dumps(r, indent=2) + "\n")
        Path(a.write + ".md").write_text(md + "\n")


if __name__ == "__main__":
    main()
