"""High-throughput streaming upload writer (LAN / WireGuard).

Tornado defaults to 64 KiB HTTP read chunks, which wastes time in Python
callbacks. Pair this writer with ``HTTPServer(chunk_size=50 MiB)``.
"""

from __future__ import annotations

import logging
import os
import queue
import threading

from aird.core.transfer_native import write_fd as native_write_fd

logger = logging.getLogger(__name__)

# Match HTTPServer chunk_size — one queue item ≈ one socket read.
DEFAULT_COALESCE_BYTES = 50 * 1024 * 1024
# ~200 MiB in flight (4 × 50 MiB) before TCP backpressure.
DEFAULT_QUEUE_MAXITEMS = 4


class FastUploadWriter:
    """Background ``os.write`` loop fed by the Tornado IOLoop thread."""

    def __init__(
        self,
        fd: int,
        *,
        coalesce_bytes: int = DEFAULT_COALESCE_BYTES,
        queue_maxitems: int = DEFAULT_QUEUE_MAXITEMS,
    ) -> None:
        self._fd = fd
        self._coalesce = max(256 * 1024, int(coalesce_bytes))
        self._q: queue.Queue[bytes | None] = queue.Queue(maxsize=max(2, queue_maxitems))
        self._recv = bytearray()
        self.error: BaseException | None = None
        self._aborted = False
        self._done = threading.Event()
        self._thread = threading.Thread(
            target=self._run, name="aird-fast-upload", daemon=True
        )
        self._thread.start()

    def _run(self) -> None:
        try:
            while True:
                item = self._q.get()
                if item is None:
                    break
                if self._aborted:
                    continue
                native_write_fd(self._fd, item)
        except Exception as exc:
            self.error = exc
            logger.warning("Fast upload writer failed: %s", exc)
        finally:
            try:
                os.close(self._fd)
            except OSError:
                pass
            self._fd = -1
            self._done.set()

    def feed(self, chunk: bytes) -> None:
        if not chunk or self.error is not None:
            return
        # Server already delivers ~chunk_size blocks; pass through when large.
        if len(chunk) >= self._coalesce and not self._recv:
            self._q.put(chunk)
            return
        self._recv.extend(chunk)
        while len(self._recv) >= self._coalesce:
            block = bytes(self._recv[: self._coalesce])
            del self._recv[: self._coalesce]
            self._q.put(block)

    def finish(self, timeout: float = 600.0) -> None:
        if self._recv:
            self._q.put(bytes(self._recv))
            self._recv.clear()
        self._q.put(None)
        self._done.wait(timeout=timeout)
        if self._thread.is_alive():
            self._thread.join(timeout=min(30.0, timeout))

    def abort(self) -> None:
        self._aborted = True
        self._recv.clear()
        while True:
            try:
                self._q.get_nowait()
            except queue.Empty:
                break
        try:
            self._q.put_nowait(None)
        except Exception:
            pass
        self._done.wait(timeout=5.0)
