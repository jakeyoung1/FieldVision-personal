"""POST /api/trackman — Trackman CSV upload, stats + AI interpretation."""
import io

import pandas as pd
from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import JSONResponse

from backend.services import errors, claude

router = APIRouter()

PITCH_COLS = [
    "Pitcher", "PitcherTeam", "PitchType", "RelSpeed", "SpinRate",
    "InducedVertBreak", "HorzBreak", "PlateLocHeight", "PlateLocSide",
    "PitchCall", "TaggedPitchType", "AutoPitchType",
    # Outcome columns — these drive strike%, whiff%, K/BB and innings.
    "KorBB", "PlayResult", "OutsOnPlay", "RunsScored",
]

# Trackman PitchCall / PlayResult vocabularies.
HITS_SET = {"Single", "Double", "Triple", "HomeRun"}
STRIKE_SET = {"StrikeCalled", "StrikeSwinging", "FoulBallNotFieldable", "InPlay"}
# A swing is any offer at the pitch; whiff rate is whiffs per *swing*, not per
# pitch, so that a pitcher who is never offered at is not credited for it.
SWING_SET = {"StrikeSwinging", "FoulBallNotFieldable", "InPlay"}


def _safe_cols(df: pd.DataFrame, cols: list[str]) -> list[str]:
    return [c for c in cols if c in df.columns]


@router.post("/trackman")
def trackman(
    file: UploadFile = File(...),
    focus: str = Form(""),
):
    try:
        if not file.filename.lower().endswith(".csv"):
            raise HTTPException(400, "Only CSV files are supported")

        content = file.file.read()
        try:
            df = pd.read_csv(io.StringIO(content.decode("utf-8", errors="replace")))
        except Exception as e:
            raise HTTPException(422, f"Could not parse CSV: {e}")

        available = _safe_cols(df, PITCH_COLS)
        if not available:
            raise HTTPException(422, "No recognized Trackman columns found. Make sure this is a Trackman export CSV.")

        df_clean = df[available].dropna(how="all")

        # Build summary stats per pitcher (also capture team if available)
        stats = {}
        if "Pitcher" in df_clean.columns:
            # Use original df to get PitcherTeam (it may not be in df_clean if not in PITCH_COLS hit)
            team_col_available = "PitcherTeam" in df.columns
            for pitcher, grp in df_clean.groupby("Pitcher"):
                pitcher_stats: dict = {"pitches": len(grp)}
                # Team affiliation
                if team_col_available:
                    team_vals = df.loc[df["Pitcher"] == pitcher, "PitcherTeam"].dropna()
                    pitcher_stats["team"] = str(team_vals.iloc[0]) if not team_vals.empty else ""
                else:
                    pitcher_stats["team"] = ""
                if "RelSpeed" in grp.columns:
                    pitcher_stats["avg_velo"] = round(float(grp["RelSpeed"].mean()), 1)
                    pitcher_stats["max_velo"] = round(float(grp["RelSpeed"].max()), 1)
                if "SpinRate" in grp.columns:
                    pitcher_stats["avg_spin"] = round(float(grp["SpinRate"].mean()), 0)
                if "PitchType" in grp.columns or "TaggedPitchType" in grp.columns:
                    col = "TaggedPitchType" if "TaggedPitchType" in grp.columns else "PitchType"
                    pitcher_stats["pitch_mix"] = grp[col].value_counts().to_dict()

                # Outcome stats. Each block is guarded independently so a
                # partial export still yields everything it can support.
                if "KorBB" in grp.columns:
                    pitcher_stats["strikeouts"] = int((grp["KorBB"] == "Strikeout").sum())
                    pitcher_stats["walks"] = int((grp["KorBB"] == "Walk").sum())
                if "PlayResult" in grp.columns:
                    pr = grp["PlayResult"].fillna("")
                    pitcher_stats["hits_allowed"] = int(pr.isin(HITS_SET).sum())
                    pitcher_stats["home_runs"] = int((pr == "HomeRun").sum())
                if "RunsScored" in grp.columns:
                    pitcher_stats["runs_scored"] = int(pd.to_numeric(grp["RunsScored"], errors="coerce").fillna(0).sum())
                if "OutsOnPlay" in grp.columns:
                    total_outs = int(pd.to_numeric(grp["OutsOnPlay"], errors="coerce").fillna(0).sum())
                    pitcher_stats["outs_recorded"] = total_outs
                    pitcher_stats["innings_pitched"] = round(total_outs / 3, 1)
                if "PitchCall" in grp.columns:
                    total = len(grp)
                    strikes = int(grp["PitchCall"].isin(STRIKE_SET).sum())
                    pitcher_stats["strike_pct"] = round(strikes / total * 100, 1) if total else 0.0
                    swings = int(grp["PitchCall"].isin(SWING_SET).sum())
                    whiffs = int((grp["PitchCall"] == "StrikeSwinging").sum())
                    pitcher_stats["whiff_pct"] = round(whiffs / swings * 100, 1) if swings else 0.0
                    pitcher_stats["swings"] = swings

                stats[str(pitcher)] = pitcher_stats

        # Build teams grouping: { teamName: [pitcherName, ...] }
        teams: dict[str, list[str]] = {}
        for pitcher, s in stats.items():
            team = s.get("team") or "Unknown Team"
            teams.setdefault(team, []).append(pitcher)

        # Build plain-text summary for AI interpretation
        summary_lines = []
        for pitcher, s in stats.items():
            line = f"{pitcher}: {s['pitches']} pitches"
            if "avg_velo" in s:
                line += f", avg {s['avg_velo']} mph (max {s['max_velo']})"
            if "avg_spin" in s:
                line += f", avg spin {int(s['avg_spin'])} rpm"
            if "pitch_mix" in s:
                mix = ", ".join(f"{k}:{v}" for k, v in list(s["pitch_mix"].items())[:4])
                line += f", mix: {mix}"
            for key, label in (("strike_pct", "strike%"), ("whiff_pct", "whiff%")):
                if key in s:
                    line += f", {label}: {s[key]}"
            for key, label in (("innings_pitched", "IP"), ("strikeouts", "K"), ("walks", "BB"),
                               ("hits_allowed", "H"), ("home_runs", "HR"), ("runs_scored", "R")):
                if key in s:
                    line += f", {label}: {s[key]}"
            summary_lines.append(line)
        summary_text = "\n".join(summary_lines)

        if not summary_text:
            raise HTTPException(422, "No pitcher data found in this CSV.")

        # AI interpretation
        interpretation = claude.interpret_pitch_metrics(summary_text, focus)

        return JSONResponse({
            "rows": len(df_clean),
            "pitchers": len(stats),
            "stats": stats,
            "teams": teams,
            "summary": summary_text,
            "interpretation": interpretation,
            "columns": available,
        })

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, f"Server error: {errors.report(e, __name__)}")
