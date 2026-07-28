"""Dedicated TCP listener for bulk transfers."""

from __future__ import annotations

import logging
import os
import socket
import threading
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from aird.core.bulk.engine import BulkEngine

logger = logging.getLogger(__name__)

_bulk_tcp_port: int = 0
_server: "BulkTcpServer | None" = None

_SOCKET_BUF = 16 * 1024 * 1024


def get_bulk_tcp_port() -> int:
    return _bulk_tcp_port


def resolve_bulk_port(http_port: int) -> int:
    raw = os.environ.get("AIRD_BULK_PORT", "").strip()
    if raw:
        return int(raw)
    return int(http_port) + 1


class BulkTcpServer:
    def __init__(self, engine: BulkEngine, host: str = "", port: int = 0) -> None:
        self._engine = engine
        self._host = host
        self._port = port
        self._sock: socket.socket | None = None
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()

    @property
    def port(self) -> int:
        return self._port

    def start(self) -> None:
        global _bulk_tcp_port, _server
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind((self._host, self._port))
        sock.listen(128)
        self._port = sock.getsockname()[1]
        self._sock = sock
        _bulk_tcp_port = self._port
        _server = self
        self._thread = threading.Thread(
            target=self._accept_loop, name="aird-bulk-tcp", daemon=True
        )
        self._thread.start()
        logger.info("Bulk TCP transfer listening on 0.0.0.0:%d", self._port)

    def stop(self) -> None:
        self._stop.set()
        if self._sock:
            try:
                self._sock.close()
            except OSError:
                pass
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=5.0)

    def _tune_client(self, sock: socket.socket) -> None:
        try:
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, _SOCKET_BUF)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, _SOCKET_BUF)
        except OSError:
            pass

    def _accept_loop(self) -> None:
        assert self._sock is not None
        while not self._stop.is_set():
            try:
                client, addr = self._sock.accept()
            except OSError:
                if self._stop.is_set():
                    break
                continue
            self._tune_client(client)
            peer = f"{addr[0]}:{addr[1]}"
            threading.Thread(
                target=self._engine.handle_tcp_socket,
                args=(client, peer),
                name=f"aird-bulk-{addr[1]}",
                daemon=True,
            ).start()
