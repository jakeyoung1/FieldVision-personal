"""Report timing: POST one finished report, read the log and summary."""
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from backend.services import auth, errors, timing

router = APIRouter()


class ReportTiming(BaseModel):
    player: str = ""
    seconds_to_report: float
    seconds_to_finish: float
    finished_by: str
    session_id: str = ""


@router.post("/timing/report", dependencies=[Depends(auth.require_scout_key)])
def record_timing(req: ReportTiming):
    try:
        return JSONResponse(timing.record(
            player=req.player, seconds_to_report=req.seconds_to_report,
            seconds_to_finish=req.seconds_to_finish, finished_by=req.finished_by,
            session_id=req.session_id))
    except ValueError as e:
        raise HTTPException(422, str(e))
    except Exception as e:
        raise HTTPException(500, f"Timing write failed: {errors.report(e, __name__)}")


@router.get("/timing/summary")
def timing_summary():
    return JSONResponse(timing.summary())


@router.get("/timing/log", dependencies=[Depends(auth.require_scout_key)])
def timing_log(limit: int = 1000):
    return JSONResponse({"rows": timing.rows(limit)})
