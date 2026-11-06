"""Read-only impersonation flag for the current request."""
from contextvars import ContextVar
from typing import Optional

current: ContextVar[Optional[dict]] = ContextVar("paiger_viewing", default=None)


def name() -> str:
    payload = current.get()
    if not payload:
        return ""
    return str(payload.get("name") or "")
