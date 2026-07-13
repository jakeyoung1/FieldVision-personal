"""FieldVision — FastAPI backend entry point."""
import os
from pathlib import Path

# Load .env if present (local dev)
try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent.parent / ".env")
except ImportError:
    pass

import time
from collections import defaultdict, deque

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from backend.routes import analyze, basketball, chat, compare, trackman, trackman_viz

# ── App ───────────────────────────────────────────────────────────────────────
app = FastAPI(
    title="FieldVision API",
    description="Baseball scouting intelligence powered by Claude + Branch Rickey RAG",
    version="2.0.0",
)

# ── CORS ─────────────────────────────────────────────────────────────────────
# Same-origin serving means CORS is only needed for cross-origin API use.
# Default stays permissive for local dev; set FV_ALLOWED_ORIGINS on the host
# (comma-separated) to lock down a public deployment.
_origins = [o.strip() for o in os.environ.get("FV_ALLOWED_ORIGINS", "*").split(",")]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_origins,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Rate limiting ─────────────────────────────────────────────────────────────
# Every POST under /api/ triggers a paid Claude call. Cheap in-memory per-IP
# sliding window; single-process deployment makes this sufficient.
RATE_LIMIT = int(os.environ.get("FV_RATE_LIMIT", "20"))        # requests
RATE_WINDOW = int(os.environ.get("FV_RATE_WINDOW", "300"))     # seconds
MAX_BODY_BYTES = 10 * 1024 * 1024                              # 10 MB uploads
_hits: dict[str, deque] = defaultdict(deque)


@app.middleware("http")
async def guardrails(request: Request, call_next):
    if request.method == "POST" and request.url.path.startswith("/api/"):
        body_len = int(request.headers.get("content-length") or 0)
        if body_len > MAX_BODY_BYTES:
            return JSONResponse({"detail": "Request too large"}, status_code=413)

        ip = request.client.host if request.client else "unknown"
        now = time.monotonic()
        window = _hits[ip]
        while window and now - window[0] > RATE_WINDOW:
            window.popleft()
        if len(window) >= RATE_LIMIT:
            return JSONResponse(
                {"detail": "Rate limit exceeded — try again in a few minutes"},
                status_code=429,
            )
        window.append(now)
    return await call_next(request)

# ── API routes ────────────────────────────────────────────────────────────────
app.include_router(analyze.router, prefix="/api")
app.include_router(chat.router,    prefix="/api")
app.include_router(trackman.router, prefix="/api")
app.include_router(basketball.router, prefix="/api")
app.include_router(trackman_viz.router, prefix="/api")
app.include_router(compare.router, prefix="/api")


@app.get("/api/health")
@app.head("/api/health")
def health():
    return {"status": "ok", "version": "2.0.0"}


@app.head("/")
def health_root():
    """HEAD support for uptime monitors hitting the root."""
    return {"status": "ok"}


# ── Static frontend ───────────────────────────────────────────────────────────
STATIC_DIR = Path(__file__).parent.parent / "static"
ROOT_HTML  = Path(__file__).parent.parent / "index.html"

if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.get("/", include_in_schema=False)
def serve_root():
    return FileResponse(str(ROOT_HTML))


@app.get("/{path:path}", include_in_schema=False)
def serve_spa(path: str):
    """Serve index.html for all non-API routes (SPA fallback)."""
    file = Path(__file__).parent.parent / path
    if file.exists() and file.is_file():
        return FileResponse(str(file))
    return FileResponse(str(ROOT_HTML))
