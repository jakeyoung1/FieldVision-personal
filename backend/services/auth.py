"""Scout key: who may write to the decision log and read player-level records.

One shared secret for the coaching staff, set as FV_SCOUT_KEY on the server
and sent by the browser in the X-Scout-Key header. It keeps strangers from
writing overrides or reading which players a staff overruled; it is not a
user-account system, and the UI does not pretend otherwise.

Fails closed: with no key configured, protected routes refuse every request.
Local development can opt out with FV_ALLOW_OPEN_WRITES=1, which must never be
set on a public deployment.
"""
import hmac
import os

from fastapi import Header, HTTPException

HEADER = "X-Scout-Key"


def _configured_key() -> str:
    return os.environ.get("FV_SCOUT_KEY", "").strip()


def _open_for_dev() -> bool:
    return os.environ.get("FV_ALLOW_OPEN_WRITES", "").strip() == "1"


def status() -> dict:
    return {"scout_key_configured": bool(_configured_key()), "open_for_dev": _open_for_dev()}


def require_scout_key(x_scout_key: str | None = Header(default=None)) -> None:
    """FastAPI dependency for protected routes."""
    expected = _configured_key()
    if not expected:
        if _open_for_dev():
            return
        raise HTTPException(503, "Scout records are disabled: FV_SCOUT_KEY is not set on this server.")
    supplied = (x_scout_key or "").strip()
    if not supplied or not hmac.compare_digest(supplied.encode(), expected.encode()):
        raise HTTPException(401, "A valid scout key is required.", headers={"WWW-Authenticate": HEADER})
