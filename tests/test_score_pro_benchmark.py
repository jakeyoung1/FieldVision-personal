"""Unit tests for evaluation/pro_benchmark/score_pro_benchmark.py.

ALL NUMBERS BELOW ARE TOY / SYNTHETIC. They are made up to check arithmetic and
are not grades of any real player or output of FieldVision. FieldVision has not
been run on the benchmark players; no benchmark result exists yet.
"""
import csv
import json

from evaluation import grade_validation as G
from evaluation.pro_benchmark import score_pro_benchmark as s

TOY_BENCH = {  # toy published grades
    "toy1": {"id": "toy1", "split": "test", "role": "hitter",
             "published": {**{k: None for k in s.ALL_KEYS}, "hit": 50.0, "power": 60.0, "overall": 50.0}},
    "toy2": {"id": "toy2", "split": "test", "role": "hitter",
             "published": {**{k: None for k in s.ALL_KEYS}, "hit": 40.0, "power": 45.0, "overall": 40.0}},
    "toy3": {"id": "toy3", "split": "dev", "role": "pitcher",
             "published": {**{k: None for k in s.ALL_KEYS}, "fastball": 60.0, "slider": 55.0,
                           "control": 45.0, "overall": 45.0}},
}


def test_letter_conversion_matches_repo_table():
    assert s.LETTER_TO_2080 == G.LETTER_TO_2080      # the scorer keeps its own copy; it must not drift
    assert s.to_2080("C") == 40 and s.to_2080("A") == 70 and s.to_2080("F") == 20
    assert s.to_2080("55") == 55 and s.to_2080("") is None


def test_perfect_agreement_toy():
    toy_out = {pid: dict(b["published"]) for pid, b in TOY_BENCH.items()}
    r = s.score(toy_out, TOY_BENCH, draws=200)
    pooled = r["by_tool"]["_pooled"]
    assert pooled["mae"] == 0 and pooled["within_step"] == 1.0 and pooled["n"] == 7


def test_known_gaps_toy():
    # toy outputs: hit +5, power -10 on toy1; hit +0, power +5 on toy2
    toy_out = {"toy1": {**{k: None for k in s.ALL_KEYS}, "hit": 55.0, "power": 50.0},
               "toy2": {**{k: None for k in s.ALL_KEYS}, "hit": 40.0, "power": 50.0}}
    m = s.metrics([p for v in s.pairs_by_player(toy_out, TOY_BENCH, "_pooled").values() for p in v])
    assert m["n"] == 4
    assert m["mae"] == (5 + 10 + 0 + 5) / 4
    assert m["bias"] == (5 - 10 + 0 + 5) / 4
    assert m["within_step"] == 3 / 4


def test_split_filter_and_placeholder(tmp_path):
    p = tmp_path / "out.csv"
    with p.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["id", "status"] + s.ALL_KEYS)
        w.writeheader()
        w.writerow({"id": "toy1", "status": "PLACEHOLDER"})
        w.writerow({"id": "toy3", "status": "fieldvision", "fastball": "B+", "control": "C+"})
    out = s.load_outputs_csv(p, TOY_BENCH)
    assert list(out) == ["toy3"] and out["toy3"]["fastball"] == 60
    assert s.score(out, TOY_BENCH, split="test")["players_scored"] == 0


def test_ai_json_tool_mapping(tmp_path):
    p = tmp_path / "ai.json"
    p.write_text(json.dumps({"grades": [{"id": "toy3", "grade": "C+",
                                         "tools": {"Fastball": "B+", "Breaking Ball": "B", "Command": "C+", "Makeup": "A"}}]}))
    out = s.load_outputs_ai_json(p, TOY_BENCH)["toy3"]
    assert out["fastball"] == 60 and out["slider"] == 55 and out["control"] == 45 and out["overall"] == 45


def test_no_outputs_reports_nothing():
    r = s.score({}, TOY_BENCH, draws=10)
    assert r["players_scored"] == 0
    assert "No FieldVision outputs" in s.to_markdown(r)


def test_scorer_reads_the_committed_public_grades():
    """The full benchmark file is local only; the scorer must work from the public copy."""
    bench = s.load_bench(s.HERE / "pro_benchmark.public.csv")
    assert len(bench) == 96
    assert {b["split"] for b in bench.values()} == {"dev", "test"}
    assert all(b["published"]["overall"] is not None for b in bench.values())
