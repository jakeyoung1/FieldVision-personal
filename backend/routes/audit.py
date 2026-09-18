"""Decision audit trail — record and read scout overrides of AI grades."""
from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from backend.services import audit, errors

router = APIRouter()


class OverrideEvent(BaseModel):
    player: str
    tool: str = "__overall__"
    ai_grade: str | None = None
    scout_grade: str | None = None
    reason: str = ""
    action: str = "set"          # "set" | "cleared"
    session_id: str = ""


@router.post("/audit/override")
def record_override(req: OverrideEvent):
    """Append one override event to the decision log.

    The frontend calls this fire-and-forget: a scout's local override must keep
    working whether or not the log accepts the write, so failures here never
    block the UI.
    """
    try:
        return JSONResponse(audit.record(
            player=req.player, tool=req.tool, ai_grade=req.ai_grade,
            scout_grade=req.scout_grade, reason=req.reason,
            action=req.action, session_id=req.session_id,
        ))
    except Exception as e:
        raise HTTPException(500, f"Audit write failed: {errors.report(e, __name__)}")


@router.get("/audit/log")
def read_log(player: str | None = None, limit: int = 100):
    try:
        return JSONResponse({"entries": audit.log(player=player, limit=limit)})
    except Exception as e:
        raise HTTPException(500, f"Audit read failed: {errors.report(e, __name__)}")


@router.get("/audit/summary")
def read_summary():
    try:
        return JSONResponse(audit.summary())
    except Exception as e:
        raise HTTPException(500, f"Audit read failed: {errors.report(e, __name__)}")
