"""Retrieval fixes and the evaluation harness. No API key required."""
import json

import pytest

from backend.services import rag
from evaluation import retrieval_eval as E

PITCHER_NOTE = (
    "Cole Braddock\nRHP, junior, 6'3 205. Fastball sits 94-96, touched 97. "
    "Slider is the out pitch. Changeup flashes average. Command ahead of control."
)
HITTER_NOTE = (
    "Dee Whitlow\nSS, sophomore, 5'10 170. Plus-plus runner. Soft hands at short. "
    "Left-handed bat, doesn't chase, sneaky pop."
)


# ── Vocabulary normalization ─────────────────────────────────────────────────
def test_normalize_maps_modern_terms_to_rickey_vocabulary():
    out = rag.normalize("Heater sits 94, changeup and breaking ball, plus command")
    assert "fastball" in out
    assert "changeup" in out
    assert "curveball" in out
    assert "control" in out


def test_normalize_maps_rickey_spellings_to_the_same_terms():
    out = rag.normalize("Good fast ball, a change of pace curve, fair control", expand_positions=False)
    assert "fastball" in out and "changeup" in out and "curveball" in out


@pytest.mark.parametrize("text,kept", [
    ("pop time 1.95 to second", "pop time"),
    ("1.9 pop times on throws", "pop times"),
    ("lazy pop fly to left", "pop fly"),
    ("two pop flies", "pop flies"),
    ("a pop up to short", "pop up"),
    ("pop out to second", "pop out"),
    ("catchable foul pop, got there late", "foul pop"),
    ("a foul pop behind the plate", "foul pop"),
    ("foul-pop to the catcher", "foul-pop"),
    ("pop foul near the dugout", "pop foul"),
    ("two pop fouls", "pop fouls"),
])
def test_pop_time_and_pop_fly_are_not_rewritten_as_power(text, kept):
    out = rag.normalize(text)
    assert kept in out and "power" not in out


def test_standalone_pop_still_means_power():
    assert rag.normalize("sneaky pop now and then") == "sneaky power now and then"
    assert rag.normalize("enough pop.") == "enough power."
    # "foul" elsewhere in the sentence does not protect an unrelated "pop".
    assert rag.normalize("no pop; foul territory") == "no power; foul territory"
    assert rag.normalize("fouls off pitches, some pop") == "fouls off pitches, some power"


def test_the_gold_note_with_foul_pop_is_no_longer_read_as_power():
    """q25 describes a catcher slow on 'a catchable foul pop'. It used to be
    indexed as 'foul power', a hitting word in a sentence about receiving."""
    note = next(q["note"] for q in E.load_gold()["queries"] if q["id"] == "q25")
    assert "foul pop" in note
    out = rag.normalize(note)
    assert "foul pop" in out and "foul power" not in out


def test_position_abbreviations_expand_only_on_the_position_line():
    note = "Tyler Renko\nRHP, junior\nHe pitched IF the bullpen was rested. OF note, he competes."
    out = rag.normalize(note)
    assert "right hand pitcher" in out
    assert "infielder" not in out
    assert "outfielder" not in out


def test_corpus_headers_are_not_read_as_positions():
    """Regression: 'GAMES OF MAY 17TH' became 'games outfielder may 17th'."""
    header = "SALINAS-CHANNEL CITIES' GAMES OF MAY 17TH AT SALINAS\nSCHNEIDER\nHas good power."
    assert "outfielder" not in rag.normalize(header, expand_positions=False)


# ── Role inference and filter ────────────────────────────────────────────────
def test_infer_role_from_position_line():
    assert rag.infer_role(PITCHER_NOTE) == rag.ROLE_PITCHER
    assert rag.infer_role(HITTER_NOTE) == rag.ROLE_HITTER


def test_infer_role_two_way_header_falls_back_to_wording():
    note = "Chuck Novak\n1B/RHP, freshman. Excellent swing, should hit, power, runner, stance."
    assert rag.infer_role(note) == rag.ROLE_HITTER


def test_roles_conflict_only_between_pitcher_and_hitter():
    assert rag._roles_conflict(rag.ROLE_PITCHER, rag.ROLE_HITTER)
    assert not rag._roles_conflict(rag.ROLE_PITCHER, rag.ROLE_MIXED)
    assert not rag._roles_conflict(rag.ROLE_UNKNOWN, rag.ROLE_HITTER)


def test_role_filter_drops_hitter_reports_for_pitcher_notes():
    hits = rag.retrieve(PITCHER_NOTE, k=10)
    assert hits
    assert all(h["role"] != rag.ROLE_HITTER for h in hits)


def test_demo_pitchers_get_fewer_wrong_role_reports_than_before():
    """The outside reviewer's finding, on the exact demo notes, by hand label."""
    cases = E.demo_cases(E.load_role_labels())
    before = sum(h["mismatch"] for c in cases for h in c["before"])
    after = sum(h["mismatch"] for c in cases for h in c["after"])
    assert after < before


# ── Duplicates, bands, context ───────────────────────────────────────────────
def test_duplicate_transcriptions_take_one_slot():
    # mss37820017-55 and -56 are the same page transcribed twice.
    assert rag.canonical_asset("mss37820017-56") == rag.canonical_asset("mss37820017-55")
    note = "Larry Jaster\nLHP. Good fastball and a good curveball, smooth easy delivery, changeup off his curve."
    hits = rag.retrieve(note, k=10)
    canon = [rag.canonical_asset(h["asset"]) for h in hits]
    assert len(canon) == len(set(canon))


@pytest.mark.parametrize("score,band", [(0.31, "strong"), (0.25, "strong"),
                                        (0.20, "moderate"), (0.15, "moderate"),
                                        (0.149, "weak"), (0.0, "weak")])
def test_confidence_band_edges(score, band):
    assert rag.confidence_band(score) == band


def _hit(score):
    return {"source": "S", "text": "t", "score": score, "confidence": rag.confidence_band(score)}


def test_weak_hits_never_reach_the_model():
    assert rag.format_context([_hit(0.12), _hit(0.10)]) == ""
    block = rag.format_context([_hit(0.30), _hit(0.12)])
    assert block.startswith("--- Branch Rickey Reference ---")
    assert block.count("[S]") == 1


def test_match_summary_reports_quality_of_best_hit():
    assert rag.match_summary([], "x")["quality"] == "none"
    s = rag.match_summary([_hit(0.18), _hit(0.11)], PITCHER_NOTE)
    assert s["quality"] == "moderate"
    assert s["used_as_evidence"] == 1
    assert s["note_role"] == rag.ROLE_PITCHER


def test_retrieve_returns_provenance_fields():
    hit = rag.retrieve(PITCHER_NOTE, k=1)[0]
    for key in ("asset", "source", "year", "scan_url", "score", "confidence", "role"):
        assert key in hit


# ── Metrics ──────────────────────────────────────────────────────────────────
def test_precision_recall_mrr():
    ranked = ["a", "x", "b", "y", "z", "c"]
    rel = {"a", "b", "c"}
    assert E.precision_at_k(ranked, rel) == pytest.approx(2 / 5)
    assert E.recall_at_k(ranked, rel) == pytest.approx(2 / 3)
    assert E.reciprocal_rank(ranked, rel) == 1.0
    assert E.reciprocal_rank(["x", "y", "b"], rel) == pytest.approx(1 / 3)
    assert E.reciprocal_rank(["x"], rel) == 0.0


def test_bm25_ranks_the_matching_document_first():
    bm = E.BM25(["slow curve change of pace", "power hitter left field", "catcher arm"])
    assert bm.scores("power hitter").argmax() == 1


# ── Gold set integrity ───────────────────────────────────────────────────────
def test_gold_set_is_well_formed_and_labeled_as_unaudited():
    gold = E.load_gold()
    assert gold["_meta"]["label_status"] == "ai_drafted_unaudited"
    assert gold["_meta"]["notes_are_synthetic"] is True
    queries = gold["queries"]
    assert 30 <= len(queries) <= 50
    assets = {d["asset"] for d in E.corpus()}
    for q in queries:
        assert q["role"] in (rag.ROLE_PITCHER, rag.ROLE_HITTER)
        assert q["seed"] in q["relevant"]
        assert set(q["relevant"]) <= assets, q["id"]


def test_role_labels_cover_known_documents_only():
    labels = E.load_role_labels()
    assets = {d["asset"] for d in E.corpus()}
    assert labels and set(labels) <= assets
    assert set(labels.values()) <= {"pitcher", "hitter", "other"}


def test_committed_results_match_a_fresh_run():
    """The numbers in the README and /methods come from this file; keep it honest."""
    committed = json.loads((E.RESULTS_DIR / "retrieval_eval.json").read_text())
    fresh = E.run_all()
    for name, s in fresh["systems"].items():
        for metric in ("precision_at_5", "recall_at_5", "mrr_at_10", "role_mismatch_at_5"):
            assert committed["systems"][name][metric] == s[metric], (name, metric)
