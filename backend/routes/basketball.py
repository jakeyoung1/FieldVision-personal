"""Basketball routes — scouting analysis, box score upload, coaching chat."""
import io

import pandas as pd
from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from backend.services import basketball, claude, files, rag_basketball

router = APIRouter()

# Column aliases: canonical → acceptable export headers
ALIASES = {
    "player": ["Player", "Name"],
    "team":   ["Tm", "Team"],
    "mp":     ["MP", "MIN", "Min"],
    "pts":    ["PTS"],
    "reb":    ["TRB", "REB"],
    "ast":    ["AST"],
    "stl":    ["STL"],
    "blk":    ["BLK"],
    "tov":    ["TOV", "TO"],
    "fg":     ["FG", "FGM"],
    "fga":    ["FGA"],
    "tp":     ["3P", "3PM"],
    "tpa":    ["3PA"],
    "ft":     ["FT", "FTM"],
    "fta":    ["FTA"],
    "fg_pct": ["FG%"],
    "tp_pct": ["3P%"],
    "ft_pct": ["FT%"],
}


def _col(df: pd.DataFrame, key: str) -> str | None:
    for name in ALIASES[key]:
        if name in df.columns:
            return name
    return None


def _summarize_players(df: pd.DataFrame) -> dict[str, dict]:
    """Aggregate box score rows into per-player stat lines."""
    player_col = _col(df, "player")
    if not player_col:
        raise HTTPException(422, "No 'Player' column found. Make sure this is a box score / stats export CSV.")

    team_col = _col(df, "team")
    stats: dict[str, dict] = {}

    for player, grp in df.groupby(player_col):
        s: dict = {"games": len(grp)}
        if team_col:
            teams = grp[team_col].dropna()
            s["team"] = str(teams.iloc[0]) if not teams.empty else ""
        else:
            s["team"] = ""

        for key, label in [("mp", "mpg"), ("pts", "ppg"), ("reb", "rpg"), ("ast", "apg"),
                           ("stl", "spg"), ("blk", "bpg"), ("tov", "topg")]:
            col = _col(df, key)
            if col:
                vals = pd.to_numeric(grp[col], errors="coerce").dropna()
                if not vals.empty:
                    s[label] = round(float(vals.mean()), 1)

        # Shooting: prefer computing from makes/attempts sums; fall back to given %
        sums = {}
        for key in ("fg", "fga", "tp", "tpa", "ft", "fta", "pts"):
            col = _col(df, key)
            if col:
                sums[key] = float(pd.to_numeric(grp[col], errors="coerce").fillna(0).sum())

        for made, att, label, pct_key in [("fg", "fga", "fg_pct", "fg_pct"),
                                          ("tp", "tpa", "tp_pct", "tp_pct"),
                                          ("ft", "fta", "ft_pct", "ft_pct")]:
            if sums.get(att, 0) > 0:
                s[label] = round(sums[made] / sums[att], 3)
            else:
                col = _col(df, pct_key)
                if col:
                    vals = pd.to_numeric(grp[col], errors="coerce").dropna()
                    if not vals.empty:
                        s[label] = round(float(vals.mean()), 3)

        # True shooting: PTS / (2 * (FGA + 0.44 * FTA))
        if sums.get("pts", 0) > 0 and (sums.get("fga", 0) + sums.get("fta", 0)) > 0:
            s["ts_pct"] = round(sums["pts"] / (2 * (sums["fga"] + 0.44 * sums["fta"])), 3)

        stats[str(player)] = s

    return stats


def _summary_text(stats: dict[str, dict]) -> str:
    lines = []
    for player, s in stats.items():
        line = f"{player}: {s['games']} game(s)"
        if "ppg" in s:
            line += f", {s['ppg']} PPG"
        if "rpg" in s:
            line += f", {s['rpg']} RPG"
        if "apg" in s:
            line += f", {s['apg']} APG"
        for key, label in [("spg", "SPG"), ("bpg", "BPG"), ("topg", "TOV")]:
            if key in s:
                line += f", {s[key]} {label}"
        for key, label in [("fg_pct", "FG"), ("tp_pct", "3P"), ("ft_pct", "FT"), ("ts_pct", "TS")]:
            if key in s:
                line += f", {s[key]:.1%} {label}"
        lines.append(line)
    return "\n".join(lines)


class CoachRequest(BaseModel):
    mode: str = "chat"        # development | opponent | practice | chat
    history: list[dict]       # [{role: user|assistant, content: str}]
    context: str = ""         # session context: reports + box score summaries


class PlayRequest(BaseModel):
    players: list[dict]       # [{id, name, position, strengths, x, y}]
    objective: str = ""


@router.post("/basketball/analyze")
async def bb_analyze(
    files_upload: list[UploadFile] | None = File(None),
    notes_text: str = Form(""),
):
    """Analyze basketball scouting notes from files and/or pasted text."""
    if not files_upload and not notes_text.strip():
        raise HTTPException(400, "No files or notes provided")

    try:
        player_map: dict[str, str] = {}

        if files_upload:
            raw_files = []
            for f in files_upload:
                content = await f.read()
                raw_files.append((f.filename, content))
            player_map.update(files.group_by_player(raw_files))

        # Pasted notes: first line = player name, '---' separates players
        if notes_text.strip():
            for block in notes_text.split("---"):
                block = block.strip()
                if not block:
                    continue
                first_line, _, rest = block.partition("\n")
                label = first_line.strip() or "Pasted Notes"
                player_map[label] = player_map.get(label, "") + "\n" + (rest or first_line)

        results = []
        for label, text in player_map.items():
            if not text.strip():
                continue
            context = rag_basketball.context_block(text[:500])
            report = basketball.analyze_notes(text, context)
            profile = claude.extract_player_profile(label, report)
            results.append({
                "label": label,
                "report": report,
                "profile": profile,
                "context_used": bool(context),
            })

        return JSONResponse({"results": results, "count": len(results)})

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, f"Server error: {str(e)}")


@router.post("/basketball/boxscore")
async def bb_boxscore(
    file: UploadFile = File(...),
    focus: str = Form(""),
):
    """Box score / season stats CSV → per-player summary + AI interpretation."""
    try:
        if not file.filename.lower().endswith(".csv"):
            raise HTTPException(400, "Only CSV files are supported")

        content = await file.read()
        try:
            df = pd.read_csv(io.StringIO(content.decode("utf-8", errors="replace")))
        except Exception as e:
            raise HTTPException(422, f"Could not parse CSV: {e}")

        stats = _summarize_players(df)
        if not stats:
            raise HTTPException(422, "No player data found in this CSV.")
        summary = _summary_text(stats)

        teams: dict[str, list[str]] = {}
        for player, s in stats.items():
            teams.setdefault(s.get("team") or "Unknown Team", []).append(player)

        interpretation = basketball.interpret_box_score(summary, focus)

        return JSONResponse({
            "rows": len(df),
            "players": len(stats),
            "stats": stats,
            "teams": teams,
            "summary": summary,
            "interpretation": interpretation,
        })

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, f"Server error: {str(e)}")


@router.post("/basketball/play")
async def bb_play(req: PlayRequest):
    """Design an animated set play from player positions + strengths."""
    if not req.players or len(req.players) > 5:
        raise HTTPException(400, "1-5 players required")
    for i, p in enumerate(req.players):
        if "x" not in p or "y" not in p:
            raise HTTPException(400, f"player {i} missing court position")
        p.setdefault("id", f"P{i + 1}")
    try:
        play = basketball.design_play(req.players, req.objective)
        return JSONResponse({"play": play})
    except ValueError as e:
        raise HTTPException(502, str(e))
    except Exception as e:
        raise HTTPException(500, f"Server error: {str(e)}")


@router.post("/basketball/coach")
async def bb_coach(req: CoachRequest):
    """Coaching chat: development plans, opponent scouting, practice plans, open chat."""
    if not req.history:
        raise HTTPException(400, "history is required")
    try:
        reply = basketball.coach_reply(req.mode, req.history, req.context)
        return JSONResponse({"reply": reply, "mode": req.mode})
    except Exception as e:
        raise HTTPException(500, f"Server error: {str(e)}")
