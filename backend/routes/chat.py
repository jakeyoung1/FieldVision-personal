"""POST /api/chat — follow-up conversation on a scouting report (optionally SSE-streamed)."""
import json

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel

from backend.services import claude

router = APIRouter()


class ChatRequest(BaseModel):
    history: list[dict]       # [{role: user|assistant, content: str}]
    context: str = ""         # The report text to ground answers in
    session_context: str = "" # Brief session summary for system prompt
    stream: bool = False


def sse(generator):
    """Wrap a text-delta generator as server-sent events."""
    def event_stream():
        try:
            for delta in generator:
                yield f"data: {json.dumps({'delta': delta})}\n\n"
            yield 'data: {"done": true}\n\n'
        except Exception as e:
            yield f"data: {json.dumps({'error': str(e)})}\n\n"
    return StreamingResponse(event_stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@router.post("/chat")
async def chat(req: ChatRequest):
    if not req.history:
        raise HTTPException(400, "history is required")

    if req.stream:
        return sse(claude.chat_reply_stream(req.history, req.context, req.session_context))

    reply = claude.chat_reply(req.history, req.context, req.session_context)
    return JSONResponse({"reply": reply})
