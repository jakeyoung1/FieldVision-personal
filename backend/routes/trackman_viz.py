"""POST /api/trackman/viz — per-pitch chart data + MLB percentiles. No LLM call."""
import io

import pandas as pd
from fastapi import APIRouter, File, HTTPException, UploadFile
from fastapi.responses import JSONResponse

from backend.services import mlb_benchmarks

router = APIRouter()

NUM_COLS = ["RelSpeed", "SpinRate", "InducedVertBreak", "HorzBreak",
            "PlateLocHeight", "PlateLocSide"]


def _pitch_type_col(df: pd.DataFrame) -> str | None:
    for c in ("TaggedPitchType", "AutoPitchType", "PitchType"):
        if c in df.columns:
            return c
    return None


def build_viz(df: pd.DataFrame) -> dict:
    if "Pitcher" not in df.columns:
        raise HTTPException(422, "No 'Pitcher' column found in this CSV.")
    type_col = _pitch_type_col(df)

    for c in NUM_COLS:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")

    pitchers = {}
    for pitcher, grp in df.groupby("Pitcher"):
        pitches = []
        for _, row in grp.iterrows():
            p = {"type": str(row[type_col]) if type_col and pd.notna(row.get(type_col)) else "Unknown"}
            for c, key in [("RelSpeed", "velo"), ("SpinRate", "spin"),
                           ("InducedVertBreak", "ivb"), ("HorzBreak", "hb"),
                           ("PlateLocHeight", "loc_z"), ("PlateLocSide", "loc_x")]:
                if c in grp.columns and pd.notna(row.get(c)):
                    p[key] = round(float(row[c]), 2)
            pitches.append(p)

        # Per-pitch-type aggregates + MLB percentiles
        types = {}
        for p in pitches:
            types.setdefault(p["type"], []).append(p)
        type_summary = []
        for t, plist in types.items():
            velos = [p["velo"] for p in plist if "velo" in p]
            spins = [p["spin"] for p in plist if "spin" in p]
            entry = {"type": t, "count": len(plist)}
            bench_key = mlb_benchmarks.match_pitch_type(t)
            if velos:
                entry["avg_velo"] = round(sum(velos) / len(velos), 1)
                entry["max_velo"] = round(max(velos), 1)
                if bench_key:
                    entry["velo_pctile"] = mlb_benchmarks.percentile(bench_key, "velo", entry["avg_velo"])
            if spins:
                entry["avg_spin"] = round(sum(spins) / len(spins))
                if bench_key:
                    entry["spin_pctile"] = mlb_benchmarks.percentile(bench_key, "spin", entry["avg_spin"])
            type_summary.append(entry)
        type_summary.sort(key=lambda e: -e["count"])

        pitchers[str(pitcher)] = {"pitches": pitches, "types": type_summary}

    return {"pitchers": pitchers, "benchmark_note":
            "Percentiles vs approximate MLB league distributions (public Statcast data)"}


@router.post("/trackman/viz")
async def trackman_viz(file: UploadFile = File(...)):
    try:
        if not file.filename.lower().endswith(".csv"):
            raise HTTPException(400, "Only CSV files are supported")
        content = await file.read()
        try:
            df = pd.read_csv(io.StringIO(content.decode("utf-8", errors="replace")))
        except Exception as e:
            raise HTTPException(422, f"Could not parse CSV: {e}")
        return JSONResponse(build_viz(df))
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, f"Server error: {str(e)}")
