"""WebSocket adapter for bulk transfers (browser path)."""

from __future__ import annotations

import logging
import threading
from collections import deque

from aird.core.bulk.engine import BulkEngine

logger = logging.getLogger(__name__)


class WsBulkTransport:
    """Queue-backed transport fed by Tornado WS binary frames."""

    def __init__(self) -> None:
        self._chunks: deque[bytes] = deque()
        self._cur = b""
        self._closed = False
        self._waiters: list[threading.Event] = []

    def feed(self, data: bytes) -> None:
        if self._closed:
            return
        self._chunks.append(data)
        waiters = self._waiters
        self._waiters = []
        for event in waiters:
            event.set()

    def close_feed(self) -> None:
        self._closed = True
        waiters = self._waiters
        self._waiters = []
        for event in waiters:
            event.set()

    def _wait_data(self) -> None:
        if self._cur or self._chunks:
            return
        if self._closed:
            raise ConnectionError("WS transport closed")
        event = threading.Event()
        self._waiters.append(event)
        event.wait(timeout=600.0)
        if not self._cur and not self._chunks and self._closed:
            raise ConnectionError("WS transport closed")

    def recv(self, nbytes: int) -> bytes:
        out = bytearray()
        while len(out) < nbytes:
            self._wait_data()
            if not self._cur and self._chunks:
                self._cur = self._chunks.popleft()
            if not self._cur:
                if self._closed:
                    break
                continue
            take = min(nbytes - len(out), len(self._cur))
            out.extend(self._cur[:take])
            self._cur = self._cur[take:]
        if len(out) < nbytes:
            raise ConnectionError("unexpected EOF")
        return bytes(out)

    def recv_into(self, buf: memoryview) -> int:
        chunk = self.recv(len(buf))
        n = len(chunk)
        if n:
            buf[:n] = chunk
        return n

    def sendall(self, data: bytes) -> None:
        raise NotImplementedError("WS bulk PUT does not receive server payload")

    def close(self) -> None:
        self.close_feed()


class WsBulkSession:
    """Run a bulk PUT session from WebSocket frames on a worker thread."""

    def __init__(self, engine: BulkEngine, on_complete) -> None:
        self._engine = engine
        self._on_complete = on_complete
        self._transport = WsBulkTransport()
        self._thread: threading.Thread | None = None
        self._started = False

    @property
    def started(self) -> bool:
        return self._started

    def feed(self, data: bytes) -> None:
        self._transport.feed(data)

    def finish_feed(self) -> None:
        self._transport.close_feed()

    def start(self, peer: str | None) -> None:
        if self._started:
            return
        self._started = True

        def _run() -> None:
            status = self._engine.execute_session(self._transport, peer)
            try:
                self._on_complete(status)
            except Exception:
                logger.debug("WS bulk on_complete failed", exc_info=True)

        self._thread = threading.Thread(target=_run, name="aird-bulk-ws", daemon=True)
        self._thread.start()
