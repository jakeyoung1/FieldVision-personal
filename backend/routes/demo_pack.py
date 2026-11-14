"""GET /api/demo-pack: the supplemental demo prospects, only when FV_DEMO_PACK=1."""
from fastapi import APIRouter
from fastapi.responses import JSONResponse

from backend.services import demo_pack

router = APIRouter()


@router.get("/demo-pack")
def get_demo_pack():
    """Flag off: one field, nothing from the pack. The page then shows only the built-in demo."""
    if not demo_pack.enabled():
        return JSONResponse({"enabled": False})
    return JSONResponse({"enabled": True, **demo_pack.load()})
