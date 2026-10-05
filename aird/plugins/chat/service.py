"""Online user tracking and chat message dispatch."""

from __future__ import annotations

import json
import logging
import threading
from typing import TYPE_CHECKING

import tornado.ioloop

from aird.plugins.chat.files import attachment_entries
from aird.plugins.chat.sanitize import plain_preview

if TYPE_CHECKING:
    from aird.plugins.chat.ws import ChatWebSocketHandler

logger = logging.getLogger(__name__)


class ChatHub:
    """Track WebSocket connections per user id (in-process)."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._by_user: dict[int, set[ChatWebSocketHandler]] = {}
        self._username: dict[int, str] = {}

    def add(self, user_id: int, handler: ChatWebSocketHandler, username: str = "") -> None:
        with self._lock:
            self._by_user.setdefault(user_id, set()).add(handler)
            if username:
                self._username[user_id] = username

    def remove(self, user_id: int, handler: ChatWebSocketHandler) -> None:
        with self._lock:
            bucket = self._by_user.get(user_id)
            if not bucket:
                return
            bucket.discard(handler)
            if not bucket:
                self._by_user.pop(user_id, None)

    def is_online(self, user_id: int) -> bool:
        with self._lock:
            return bool(self._by_user.get(user_id))

    def online_user_ids(self) -> set[int]:
        with self._lock:
            return {uid for uid, bucket in self._by_user.items() if bucket}

    def username_for(self, user_id: int) -> str | None:
        with self._lock:
            return self._username.get(user_id)

    def _schedule_send(self, handler: ChatWebSocketHandler, raw: str) -> None:
        loop = tornado.ioloop.IOLoop.current(instance=False)
        if loop is None:

            def _direct() -> None:
                try:
                    handler.write_message(raw)
                except Exception:
                    logger.debug("chat ws send failed", exc_info=True)

            _direct()
            return

        def _write() -> None:
            try:
                handler.write_message(raw)
            except Exception:
                logger.debug("chat ws send failed", exc_info=True)

        try:
            loop.add_callback(_write)
        except RuntimeError:
            _write()

    def send_to_user(self, user_id: int, payload: dict) -> None:
        with self._lock:
            targets = list(self._by_user.get(user_id, ()))
        raw = json.dumps(payload)
        for handler in targets:
            self._schedule_send(handler, raw)

    def broadcast_user_ids(
        self,
        user_ids: list[int],
        payload: dict,
        *,
        exclude_user_id: int | None = None,
    ) -> None:
        for uid in user_ids:
            if exclude_user_id is not None and uid == exclude_user_id:
                continue
            self.send_to_user(int(uid), payload)


_chat_hub = ChatHub()


def get_chat_hub() -> ChatHub:
    return _chat_hub


def _attachment_names(meta: dict) -> list[str]:
    names = [str(a.get("original_name") or "") for a in attachment_entries(meta)]
    return [n for n in names if n]


def _preview_with_attachments(body: str, names: list[str], meta: dict) -> str:
    if body:
        if len(names) == 1:
            return f"{body} · {names[0]}"
        return f"{body} · {len(names)} files"
    if len(names) == 1:
        folder = (attachment_entries(meta)[0] or {}).get("media_kind") == "folder"
        return f"Shared {names[0]}{'/' if folder else ''}"
    return f"Shared {len(names)} files"


def _preview_by_msg_type(msg_type: str | None, body: str) -> str:
    if msg_type == "text":
        return body
    if msg_type == "gif":
        return "Sent a GIF"
    if msg_type == "file":
        return "Shared a file"
    return "New message"


def message_preview(msg: dict) -> str:
    from aird.plugins.chat.e2e import ENCRYPTED_PREVIEW, is_e2e_meta

    meta = msg.get("metadata") or {}
    if is_e2e_meta(meta) or msg.get("e2e"):
        return ENCRYPTED_PREVIEW
    names = _attachment_names(meta)
    body = plain_preview(msg.get("body") or "")
    if names:
        return _preview_with_attachments(body, names, meta)
    return _preview_by_msg_type(msg.get("msg_type"), body)
