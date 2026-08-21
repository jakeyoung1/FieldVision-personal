"""POST /api/analyze — single or batch scouting note analysis."""
from concurrent.futures import ThreadPoolExecutor
from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from backend.services import errors, claude, files, rag, scout_report

router = APIRouter()


class ExtractPlayersRequest(BaseModel):
    reply: str
    context: str = ""


@router.post("/extract-players")
def extract_players(req: ExtractPlayersRequest):
    """Extract structured player profiles from a chat reply."""
    if not req.reply.strip():
        return JSONResponse({"profiles": []})
    try:
        profiles = claude.extract_players_from_chat(req.reply, req.context)
        return JSONResponse({"profiles": profiles})
    except Exception as e:
        raise HTTPException(500, f"Server error: {errors.report(e, __name__)}")


@router.post("/analyze")
def analyze(
    files_upload: list[UploadFile] = File(...),
    batch_mode: bool = Form(False),
    session_id: str = Form(""),
    trackman_context: str = Form(""),
):
    """
    Analyze one or more scouting note files.
    Returns list of player results with Evidence Chain structure.
    """
    if not files_upload:
        raise HTTPException(400, "No files uploaded")

    try:
        raw_files = []
        for f in files_upload:
            content = f.file.read()
            raw_files.append((f.filename, content))

        # Group multi-page PDFs by player name
        player_map = files.group_by_player(raw_files)

        def evaluate_one(item: tuple[str, str]) -> dict | None:
            label, text = item
            if not text.strip():
                return None
            context = rag.context_block(text[:500])

            # Evidence Chain evaluation; legacy two-call path is the fallback.
            structured = None
            try:
                structured = scout_report.evaluate(
                    text, context, trackman_context[:8_000]
                )
                report = scout_report.to_markdown(structured)
                profile = {
                    "name": structured["name"] or label,
                    "position": structured["position"],
                    "grade": structured["grade"],
                    "strengths": structured["strengths"],
                    "concerns": structured["concerns"],
                    "summary": structured["summary"][:200],
                }
            except Exception as exc:
                # The Evidence Chain is the whole point of this endpoint, so a
                # silent downgrade to the legacy path is worse than the failure
                # itself: the response still returns 200, just with
                # structured=None and no grades, evidence or confidence. Log
                # loudly so the degradation is visible in the platform log.
                errors.report(exc, f"{__name__}:evidence-chain-fallback:{label}")
                structured = None
                report = claude.analyze_notes(text, context)
                profile = claude.extract_player_profile(label, report)

            return {
                "label": label,
                "report": report,
                "profile": profile,
                "structured": structured,
                "evidence_chain": structured is not None,
                "context_used": bool(context),
            }

        # Each player is an independent, network-bound evaluation, so run them
        # concurrently: a five-player upload finishes in roughly the time of
        # the slowest single player instead of the sum of all five. Bounded so
        # a large batch cannot open an unlimited number of API connections.
        items = list(player_map.items())
        if len(items) == 1:
            results = [r for r in (evaluate_one(items[0]),) if r]
        else:
            with ThreadPoolExecutor(max_workers=min(4, len(items))) as pool:
                results = [r for r in pool.map(evaluate_one, items) if r]

        return JSONResponse({"results": results, "count": len(results)})

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, f"Server error: {errors.report(e, __name__)}")
