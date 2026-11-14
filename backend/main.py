"""FieldVision — FastAPI backend entry point."""
import os
from pathlib import Path

# Load .env if present (local dev). The test suite sets FV_SKIP_DOTENV so a
# developer's .env, which can hold the production DATABASE_URL, never reaches
# a test run.
if not os.environ.get("FV_SKIP_DOTENV"):
    try:
        from dotenv import load_dotenv
        load_dotenv(Path(__file__).parent.parent / ".env")
    except ImportError:
        pass

import time
from collections import defaultdict, deque

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from backend.routes import (analyze, audit, basketball, chat, compare, demo_pack, methods, timing,
                            trackman, trackman_viz)

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
app.include_router(audit.router, prefix="/api")
app.include_router(timing.router, prefix="/api")
app.include_router(demo_pack.router, prefix="/api")   # empty unless FV_DEMO_PACK=1
app.include_router(methods.router)   # /methods page + /api/methods data


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


@app.get("/favicon.ico", include_in_schema=False)
def favicon():
    """Browsers ask for this path whatever the page declares; it used to 404."""
    return FileResponse(str(STATIC_DIR / "favicon.ico"), media_type="image/x-icon")


ROBOTS_TXT = """User-agent: *
Allow: /
Disallow: /api/
"""


@app.get("/robots.txt", include_in_schema=False)
def robots():
    """Pages may be crawled. The API costs money per call and returns no pages."""
    return PlainTextResponse(ROBOTS_TXT)


NOT_FOUND_HTML = Path(__file__).parent.parent / "not_found.html"


@app.exception_handler(StarletteHTTPException)
async def not_found(request: Request, exc: StarletteHTTPException):
    """Real 404s instead of serving the homepage for every unknown URL.

    The old SPA fallback answered any path with index.html and a 200, so a
    typo, a dead link, or /api/anything looked like a working page and search
    engines indexed duplicates. The app navigates by #anchors, so no client
    route needs a server fallback. API callers get JSON; browsers get a page.
    The URL is still never mapped onto the filesystem.
    """
    if exc.status_code != 404:
        return JSONResponse({"detail": exc.detail}, status_code=exc.status_code, headers=exc.headers)
    if request.url.path.startswith("/api/"):
        detail = exc.detail if exc.detail != "Not Found" else "No such API route"
        return JSONResponse({"detail": detail}, status_code=404)
    return FileResponse(str(NOT_FOUND_HTML), status_code=404)
