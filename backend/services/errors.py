"""Exception reporting that preserves the underlying cause.

`anthropic.APIConnectionError.__str__()` returns the bare string
"Connection error." — the actual reason (DNS failure, TLS problem, refused
connection, timeout) lives on `__cause__`. Routes that render `str(e)` to the
client therefore report nothing actionable, and because the exception is
caught, no traceback reaches the server log either. This module fixes both
ends: a readable one-line summary for the response, and a full traceback on
stderr for the platform log.
"""
from __future__ import annotations

import os
import sys
import traceback

# Substrings that must never appear in a message returned to a client.
_SENSITIVE = ("sk-ant-", "api_key", "authorization")


def _redact(text: str) -> str:
    lowered = text.lower()
    for marker in _SENSITIVE:
        if marker in lowered:
            return "[redacted: message referenced a credential]"
    return text


def describe(exc: BaseException) -> str:
    """One-line summary including the exception chain.

    Example: "APIConnectionError: Connection error. <- ConnectError: [Errno -2]
    Name or service not known"
    """
    parts: list[str] = []
    seen: set[int] = set()
    cur: BaseException | None = exc
    while cur is not None and id(cur) not in seen and len(parts) < 4:
        seen.add(id(cur))
        message = str(cur).strip() or "(no message)"
        parts.append(f"{type(cur).__name__}: {message}")
        cur = cur.__cause__ or cur.__context__
    return _redact(" <- ".join(parts))


def report(exc: BaseException, context: str = "") -> str:
    """Log the full traceback, return a redacted summary for the client."""
    label = f"[{context}] " if context else ""
    print(f"{label}{describe(exc)}", file=sys.stderr, flush=True)
    traceback.print_exception(type(exc), exc, exc.__traceback__, file=sys.stderr)
    sys.stderr.flush()
    return describe(exc)


def key_status() -> str:
    """Shape-only report on the configured credential. Never returns the key."""
    key = os.environ.get("ANTHROPIC_API_KEY")
    if key is None:
        return "ANTHROPIC_API_KEY: not set"
    if not key.strip():
        return "ANTHROPIC_API_KEY: set but empty"
    return (
        f"ANTHROPIC_API_KEY: set (length {len(key)}, "
        f"prefix_ok={key.startswith('sk-ant-')}, "
        f"whitespace={key != key.strip()})"
    )
