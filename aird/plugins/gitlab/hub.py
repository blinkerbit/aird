"""In-process fan-out for file comment WebSockets."""

from __future__ import annotations

import json
import logging
import threading

import tornado.ioloop

logger = logging.getLogger(__name__)

_hub = None
_lock = threading.Lock()


class CommentHub:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._by_key: dict[str, set] = {}

    @staticmethod
    def key(owner: str, path: str) -> str:
        return f"{owner}\0{(path or '').replace(chr(92), '/').strip('/')}"

    def add(self, owner: str, path: str, handler) -> None:
        k = self.key(owner, path)
        with self._lock:
            self._by_key.setdefault(k, set()).add(handler)

    def remove(self, owner: str, path: str, handler) -> None:
        k = self.key(owner, path)
        with self._lock:
            bucket = self._by_key.get(k)
            if not bucket:
                return
            bucket.discard(handler)
            if not bucket:
                self._by_key.pop(k, None)

    def publish(self, owner: str, path: str, payload: dict) -> None:
        k = self.key(owner, path)
        with self._lock:
            targets = list(self._by_key.get(k, ()))
        raw = json.dumps(payload)
        loop = tornado.ioloop.IOLoop.current(instance=False)

        def _write(handler, body: str) -> None:
            try:
                handler.write_message(body)
            except Exception:
                logger.debug("file comment ws send failed", exc_info=True)

        for handler in targets:
            if loop is None:
                _write(handler, raw)
            else:
                loop.add_callback(_write, handler, raw)


def get_comment_hub() -> CommentHub:
    global _hub
    with _lock:
        if _hub is None:
            _hub = CommentHub()
        return _hub
