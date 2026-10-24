"""Voice/video calls via LiveKit (media runs outside Aird)."""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

from aird.plugins.calls.config import livekit_configured
from aird.plugins.chat import chat_library_available

PLUGIN_CALLS = "voice_video"


def calls_library_available() -> bool:
    return chat_library_available() and livekit_configured()


def is_calls_enabled() -> bool:
    from aird.utils.util import is_feature_enabled

    from aird.plugins.chat import is_chat_enabled

    return (
        calls_library_available()
        and is_chat_enabled()
        and is_feature_enabled("voice_video", False)
    )


def register_calls(routes: list) -> None:
    if not chat_library_available():
        return
    from aird.plugins.calls.handlers import CallRingHandler, ChatCallHandler

    cid = r"([^/]+)"
    routes.append((r"/api/chat/call-ring", CallRingHandler))
    routes.append((rf"/api/chat/conversations/{cid}/call", ChatCallHandler))
