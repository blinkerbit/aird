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
        ChatConversationE2EHandler,
        ChatConversationMessagesHandler,
        ChatConversationsHandler,
        ChatE2EKeysHandler,
        ChatForwardHandler,
        ChatMembersHandler,
        ChatMessageFileHandler,
        ChatMessageHandler,
        ChatMuteHandler,
        ChatPageHandler,
        ChatPinHandler,
        ChatReactionHandler,
        ChatSaveCopyHandler,
        ChatSearchHandler,
        ChatSharedWithMeDeleteHandler,
        ChatSharedWithMeHandler,
        ChatUnreadHandler,
    )
    from aird.plugins.chat.ws import ChatWebSocketHandler

    cid = r"([^/]+)"
    mid = r"([^/]+)"
    routes.extend(
        [
            (r"/chat", ChatPageHandler),
            (r"/api/chat/conversations", ChatConversationsHandler),
            (r"/api/chat/e2e/keys", ChatE2EKeysHandler),
            (rf"/api/chat/conversations/{cid}/e2e", ChatConversationE2EHandler),
            (rf"/api/chat/conversations/{cid}/messages/{mid}/file", ChatMessageFileHandler),
            (rf"/api/chat/conversations/{cid}/messages/{mid}/save-copy", ChatSaveCopyHandler),
            (rf"/api/chat/conversations/{cid}/messages/{mid}/reactions", ChatReactionHandler),
            (rf"/api/chat/conversations/{cid}/messages/{mid}/pin", ChatPinHandler),
            (rf"/api/chat/conversations/{cid}/messages/{mid}/forward", ChatForwardHandler),
            (rf"/api/chat/conversations/{cid}/messages/{mid}", ChatMessageHandler),
            (rf"/api/chat/conversations/{cid}/messages", ChatConversationMessagesHandler),
            (rf"/api/chat/conversations/{cid}/attach", ChatAttachHandler),
            (rf"/api/chat/conversations/{cid}/members", ChatMembersHandler),
            (rf"/api/chat/conversations/{cid}/mute", ChatMuteHandler),
            (r"/api/chat/search", ChatSearchHandler),
            (rf"/api/chat/shared-with-me/{mid}", ChatSharedWithMeDeleteHandler),
            (r"/api/chat/shared-with-me", ChatSharedWithMeHandler),
            (r"/api/chat/unread", ChatUnreadHandler),
            (r"/ws/chat", ChatWebSocketHandler),
        ]
    )
