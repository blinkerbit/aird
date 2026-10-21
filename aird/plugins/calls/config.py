"""LiveKit connection settings (env only — not stored in the browser)."""

from __future__ import annotations

import os
from urllib.parse import urlparse


def livekit_url() -> str:
    return (os.environ.get("LIVEKIT_URL") or "").strip().rstrip("/")


def livekit_api_key() -> str:
    return (os.environ.get("LIVEKIT_API_KEY") or "").strip()


def livekit_api_secret() -> str:
    return (os.environ.get("LIVEKIT_API_SECRET") or "").strip()


def livekit_configured() -> bool:
    return bool(livekit_url() and livekit_api_key() and livekit_api_secret())


def room_name(conversation_id: str) -> str:
    safe = "".join(c if c.isalnum() or c in "-_" else "-" for c in conversation_id)
    return f"aird-{safe}"


def livekit_ws_origin() -> str:
    """Origin for CSP connect-src (wss/ws host)."""
    raw = livekit_url()
    if not raw:
        return ""
    parsed = urlparse(raw if "://" in raw else f"https://{raw}")
    if not parsed.scheme or not parsed.netloc:
        return ""
    return f"{parsed.scheme}://{parsed.netloc}"
