"""Direct messages plugin (requires pip install aird[chat])."""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

_CHAT_AVAILABLE = False

try:
    import bleach  # noqa: F401

    _CHAT_AVAILABLE = True
except ImportError:
    logger.debug("chat extra not installed (bleach missing)")


def chat_library_available() -> bool:
    """True when ``pip install aird[chat]`` pulled bleach."""
    return _CHAT_AVAILABLE


def is_chat_enabled() -> bool:
    from aird.utils.util import is_feature_enabled

    return chat_library_available() and is_feature_enabled("direct_messages", False)


def register_chat(routes: list) -> None:
    """Append chat HTTP/WebSocket routes when the optional dependency is present."""
    if not chat_library_available():
        return
    from aird.plugins.chat.handlers import (
        ChatAttachHandler,
        ChatConversationMessagesHandler,
        ChatConversationsHandler,
        ChatMessageDeleteHandler,
        ChatPageHandler,
        ChatSharedWithMeDeleteHandler,
        ChatSharedWithMeHandler,
        ChatUnreadHandler,
    )
    from aird.plugins.chat.ws import ChatWebSocketHandler

    routes.extend(
        [
            (r"/chat", ChatPageHandler),
            (r"/api/chat/conversations", ChatConversationsHandler),
            (r"/api/chat/conversations/([0-9]+)/messages/([0-9]+)", ChatMessageDeleteHandler),
            (r"/api/chat/conversations/([0-9]+)/messages", ChatConversationMessagesHandler),
            (r"/api/chat/conversations/([0-9]+)/attach", ChatAttachHandler),
            (r"/api/chat/shared-with-me/([0-9]+)", ChatSharedWithMeDeleteHandler),
            (r"/api/chat/shared-with-me", ChatSharedWithMeHandler),
            (r"/api/chat/unread", ChatUnreadHandler),
            (r"/ws/chat", ChatWebSocketHandler),
        ]
    )
