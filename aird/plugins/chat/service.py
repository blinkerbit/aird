"""Online user tracking and chat message dispatch."""

from __future__ import annotations

import json
import logging
import os
import threading
from typing import TYPE_CHECKING

import tornado.ioloop

from aird.plugins.chat.files import attachment_entries
from aird.plugins.chat.sanitize import plain_preview

if TYPE_CHECKING:
    from aird.plugins.chat.ws import ChatWebSocketHandler

logger = logging.getLogger(__name__)


class ChatHub:
    """Track WebSocket connections per user id.

    Sockets stay in the worker that accepted them. Presence and deliveries
    are repeated to other workers through the chat hub bus.
    """

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._by_user: dict[int, set[ChatWebSocketHandler]] = {}
        self._username: dict[int, str] = {}
        self._remote: dict[int, dict[int, str]] = {}
        self._remote_visible: dict[int, set[int]] = {}
        self._loop: tornado.ioloop.IOLoop | None = None

    def add(self, user_id: int, handler: ChatWebSocketHandler, username: str = "") -> None:
        try:
            loop = tornado.ioloop.IOLoop.current(instance=False)
            if loop is not None and loop.asyncio_loop.is_running():
                self._loop = loop
        except Exception:
            pass
        with self._lock:
            self._by_user.setdefault(user_id, set()).add(handler)
            if username:
                self._username[user_id] = username
        self.publish_presence()

    def remove(self, user_id: int, handler: ChatWebSocketHandler) -> None:
        with self._lock:
            bucket = self._by_user.get(user_id)
            if not bucket:
                return
            bucket.discard(handler)
            if not bucket:
                self._by_user.pop(user_id, None)
        self.publish_presence()

    def _remote_has(self, user_id: int) -> bool:
        return any(user_id in bucket for bucket in self._remote.values())

    def is_online(self, user_id: int) -> bool:
        with self._lock:
            return bool(self._by_user.get(user_id)) or self._remote_has(user_id)

    def connected_user_ids(self) -> set[int]:
        """Every signed-in socket, including a phone that is not on screen."""
        with self._lock:
            ids = {uid for uid, sockets in self._by_user.items() if sockets}
            for bucket in self._remote.values():
                ids.update(bucket)
            return ids

    def online_user_ids(self) -> set[int]:
        """Users shown as online. A connected phone with a locked screen is not included."""
        with self._lock:
            ids = {uid for uid, bucket in self._by_user.items() if self._bucket_visible(bucket)}
            for bucket in self._remote_visible.values():
                ids.update(bucket)
            return ids

    def is_visible(self, user_id: int) -> bool:
        with self._lock:
            if self._bucket_visible(self._by_user.get(user_id, ())):
                return True
            return any(user_id in bucket for bucket in self._remote_visible.values())

    def set_available(self, handler: ChatWebSocketHandler, available: bool) -> tuple[bool, bool]:
        """Mark one socket available. Return whether this user was and is shown online."""
        with self._lock:
            user_id = next(
                (uid for uid, bucket in self._by_user.items() if handler in bucket),
                None,
            )
            was = self._visible_locked(user_id) if user_id is not None else False
            handler._available = bool(available)
            now = self._visible_locked(user_id) if user_id is not None else False
        self.publish_presence()
        return was, now

    @staticmethod
    def _bucket_visible(bucket) -> bool:
        return any(getattr(handler, "_available", True) for handler in bucket)

    def _visible_locked(self, user_id: int | None) -> bool:
        if user_id is None:
            return False
        if self._bucket_visible(self._by_user.get(user_id, ())):
            return True
        return any(user_id in bucket for bucket in self._remote_visible.values())

    def username_for(self, user_id: int) -> str | None:
        with self._lock:
            name = self._username.get(user_id)
            if name:
                return name
            for bucket in self._remote.values():
                found = bucket.get(user_id)
                if found:
                    return found
            return None

    def online_usernames(self) -> set[str]:
        with self._lock:
            names = {
                self._username[user_id]
                for user_id, sockets in self._by_user.items()
                if sockets and user_id in self._username
            }
            for bucket in self._remote.values():
                names.update(name for name in bucket.values() if name)
            return names

    def publish_presence(self) -> None:
        with self._lock:
            online = [
                {"id": uid, "username": self._username.get(uid, "")}
                for uid, sockets in self._by_user.items()
                if sockets
            ]
            visible = [
                uid
                for uid, sockets in self._by_user.items()
                if self._bucket_visible(sockets)
            ]
        self._bus({
            "op": "presence",
            "worker": os.getpid(),
            "online": online,
            "visible": visible,
        })

    def handle_bus_frame(self, frame: dict) -> None:
        op = frame.get("op")
        if op == "presence":
            worker = frame.get("worker")
            if worker == os.getpid():
                return
            mapped: dict[int, str] = {}
            for item in frame.get("online") or []:
                if not isinstance(item, dict):
                    continue
                try:
                    mapped[int(item["id"])] = str(item.get("username") or "")
                except (KeyError, TypeError, ValueError):
                    continue
            if "visible" in frame:
                shown: set[int] = set()
                for uid in frame.get("visible") or []:
                    try:
                        shown.add(int(uid))
                    except (TypeError, ValueError):
                        continue
            else:
                shown = set(mapped)
            with self._lock:
                self._remote[worker] = mapped
                self._remote_visible[worker] = shown
            return
        if op != "deliver":
            return
        payload = frame.get("payload")
        if not isinstance(payload, dict):
            return
        ids: list[int] = []
        for uid in frame.get("user_ids") or []:
            try:
                ids.append(int(uid))
            except (TypeError, ValueError):
                continue
        self._deliver_local(ids, payload)

    def _bus(self, frame: dict) -> None:
        from aird.plugins.chat.hub_bus import publish

        publish(frame)

    def _schedule_send(self, handler: ChatWebSocketHandler, raw: str) -> None:
        """Always write on the worker's IOLoop thread.

        Bus frames arrive on a helper thread. Tornado sockets are not thread
        safe, and writing from another thread does not wake the loop, so the
        frame would sit until some unrelated event arrived.
        """

        def _write() -> None:
            try:
                handler.write_message(raw)
            except Exception:
                logger.debug("chat ws send failed", exc_info=True)

        loop = self._loop
        if loop is None:
            try:
                loop = tornado.ioloop.IOLoop.current(instance=False)
            except Exception:
                loop = None
        if loop is None:
            _write()
            return
        try:
            if not loop.asyncio_loop.is_running():
                _write()
                return
            loop.add_callback(_write)
        except RuntimeError:
            _write()

    def _deliver_local(self, user_ids: list[int], payload: dict) -> None:
        raw = json.dumps(payload)
        targets: list[ChatWebSocketHandler] = []
        with self._lock:
            for uid in user_ids:
                targets.extend(self._by_user.get(uid, ()))
        for handler in targets:
            self._schedule_send(handler, raw)

    def _publish_deliver(self, user_ids: list[int], payload: dict) -> None:
        if not user_ids:
            return
        self._bus({"op": "deliver", "user_ids": user_ids, "payload": payload})

    def send_to_user(self, user_id: int, payload: dict) -> None:
        ids = [int(user_id)]
        self._deliver_local(ids, payload)
        self._publish_deliver(ids, payload)

    def broadcast_user_ids(
        self,
        user_ids: list[int],
        payload: dict,
        *,
        exclude_user_id: int | None = None,
    ) -> None:
        ids: list[int] = []
        for uid in user_ids:
            parsed = int(uid)
            if exclude_user_id is not None and parsed == int(exclude_user_id):
                continue
            ids.append(parsed)
        if not ids:
            return
        self._deliver_local(ids, payload)
        self._publish_deliver(ids, payload)


_chat_hub = ChatHub()


def get_chat_hub() -> ChatHub:
    return _chat_hub


def _attachment_names(meta: dict) -> list[str]:
    names = [str(a.get("original_name") or "") for a in attachment_entries(meta)]
    return [n for n in names if n]


def _preview_with_attachments(body: str, names: list[str], meta: dict) -> str:
    entries = attachment_entries(meta)
    kind = (entries[0] or {}).get("media_kind") if len(entries) == 1 else None
    if kind == "voice":
        return f"{body} · Voice message" if body else "Voice message"
    if body:
        if len(names) == 1:
            return f"{body} · {names[0]}"
        return f"{body} · {len(names)} files"
    if len(names) == 1:
        folder = kind == "folder"
        return f"Shared {names[0]}{'/' if folder else ''}"
    return f"Shared {len(names)} files"


def _preview_by_msg_type(msg_type: str | None, body: str) -> str:
    if msg_type == "text":
        return body
    if msg_type == "gif":
        return "Sent a GIF"
    if msg_type == "file":
        return "Shared a file"
    if msg_type == "card":
        return "Shared a card"
    return "New message"


def message_preview(msg: dict) -> str:
    from aird.plugins.chat.e2e import ENCRYPTED_PREVIEW, is_e2e_meta

    meta = msg.get("metadata") or {}
    if msg.get("msg_type") == "card":
        card = meta.get("card") if isinstance(meta, dict) else None
        if isinstance(card, dict):
            kind = str(card.get("kind") or "").strip().lower()
            title = str(card.get("title") or "").strip()
            prefix = {"poll": "Poll", "list": "List", "meet": "Meet"}.get(kind, "Card")
            return f"{prefix}: {title}" if title else prefix
    if is_e2e_meta(meta) or msg.get("e2e"):
        return ENCRYPTED_PREVIEW
    names = _attachment_names(meta)
    body = plain_preview(msg.get("body") or "")
    if names:
        return _preview_with_attachments(body, names, meta)
    return _preview_by_msg_type(msg.get("msg_type"), body)
