"""Play JSON validation — _clean_play must survive malformed model output."""
from backend.services.basketball import _clean_play

PLAYERS = [
    {"id": "P1", "x": 0.5, "y": 0.85},
    {"id": "P2", "x": 0.85, "y": 0.6},
]


def test_clamps_out_of_bounds_coords():
    play = {
        "play_name": "Test", "duration": 8,
        "players": [{"id": "P1", "keyframes": [{"t": 0, "x": -0.5, "y": 1.7}]}],
        "ball": {"keyframes": [{"t": 0, "x": 2.0, "y": -1.0, "holder": "P1"}]},
    }
    out = _clean_play(play, PLAYERS)
    kf = out["players"][0]["keyframes"][0]
    assert kf["x"] == 0.02 and kf["y"] == 0.98
    bkf = out["ball"]["keyframes"][0]
    assert bkf["x"] == 0.98 and bkf["y"] == 0.02


def test_sorts_keyframes_by_time():
    play = {
        "players": [{"id": "P1", "keyframes": [
            {"t": 5, "x": 0.1, "y": 0.1}, {"t": 0, "x": 0.5, "y": 0.5}]}],
        "ball": {"keyframes": [{"t": 0, "x": 0.5, "y": 0.5}]},
    }
    out = _clean_play(play, PLAYERS)
    ts = [kf["t"] for kf in out["players"][0]["keyframes"]]
    assert ts == sorted(ts)


def test_missing_player_gets_static_keyframe():
    play = {"players": [{"id": "P1", "keyframes": [{"t": 0, "x": 0.5, "y": 0.85}]}],
            "ball": {"keyframes": [{"t": 0, "x": 0.5, "y": 0.85}]}}
    out = _clean_play(play, PLAYERS)
    ids = [p["id"] for p in out["players"]]
    assert ids == ["P1", "P2"]
    p2 = out["players"][1]["keyframes"]
    assert p2 == [{"t": 0.0, "x": 0.85, "y": 0.6}]


def test_garbage_keyframes_skipped():
    play = {"players": [{"id": "P1", "keyframes": [
        {"t": "x", "x": None, "y": 0}, {"t": 1, "x": 0.4, "y": 0.4}]}],
        "ball": {}}
    out = _clean_play(play, PLAYERS)
    assert len(out["players"][0]["keyframes"]) == 1


def test_duration_clamped():
    assert _clean_play({"duration": 500, "players": [], "ball": {}}, PLAYERS)["duration"] == 15.0
    assert _clean_play({"duration": 1, "players": [], "ball": {}}, PLAYERS)["duration"] == 4.0


def test_ball_defaults_to_first_player():
    out = _clean_play({"players": [], "ball": {}}, PLAYERS)
    bkf = out["ball"]["keyframes"][0]
    assert (bkf["x"], bkf["y"]) == (0.5, 0.85)
    assert bkf["holder"] == "P1"
