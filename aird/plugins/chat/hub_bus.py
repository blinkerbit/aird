"""Fan chat WebSocket events out to every aird worker.

Tornado's multi-process mode keeps each worker's sockets in that process.
A single hub accepts one connection per worker and repeats each frame to the
other workers. The hub process is forked before the workers so it stays up
for the life of the service.
"""

from __future__ import annotations

import json
import logging
import os
import socket
import struct
import threading

logger = logging.getLogger(__name__)

_MAX_FRAME = 8 * 1024 * 1024
_HDR = struct.Struct(">I")

_addr: bytes | str | None = None
_listen_sock: socket.socket | None = None
_close_inherited = False
_pub_lock = threading.Lock()
_pub_sock: socket.socket | None = None
_client_started = False
_clients_lock = threading.Lock()
_clients: list[socket.socket] = []


def production_addr(port: int) -> bytes:
    """Linux abstract socket. It disappears when the hub process exits."""
    return f"\0aird-chat-{int(port)}".encode()


def _bind(addr: bytes | str) -> socket.socket:
    if isinstance(addr, str) and os.path.exists(addr):
        os.unlink(addr)
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.bind(addr)
    if isinstance(addr, str):
        os.chmod(addr, 0o600)
    sock.listen(64)
    return sock


def _drop(sock: socket.socket) -> None:
    with _clients_lock:
        if sock in _clients:
            _clients.remove(sock)
    try:
        sock.close()
    except OSError:
        pass


def _fanout(sender: socket.socket, packet: bytes) -> None:
    with _clients_lock:
        targets = [sock for sock in _clients if sock is not sender]
    for sock in targets:
        try:
            sock.sendall(packet)
        except OSError:
            logger.debug("chat hub fanout failed", exc_info=True)
            _drop(sock)


def _read_frames(conn: socket.socket):
    buf = b""
    while True:
        try:
            chunk = conn.recv(65536)
        except OSError:
            return
        if not chunk:
            return
        buf += chunk
        while len(buf) >= 4:
            (size,) = _HDR.unpack_from(buf)
            if size < 2 or size > _MAX_FRAME:
                raise ValueError(f"chat hub frame size {size}")
            if len(buf) < 4 + size:
                break
            body = buf[4 : 4 + size]
            buf = buf[4 + size :]
            yield body


def _client_thread(conn: socket.socket) -> None:
    try:
        for body in _read_frames(conn):
            _fanout(conn, _HDR.pack(len(body)) + body)
    except Exception:
        logger.debug("chat hub client closed", exc_info=True)
    finally:
        _drop(conn)


def _accept_loop(listen: socket.socket) -> None:
    while True:
        try:
            conn, _addr_in = listen.accept()
        except OSError:
            return
        with _clients_lock:
            _clients.append(conn)
        threading.Thread(
            target=_client_thread, args=(conn,), name="aird-chat-hub-io", daemon=True
        ).start()


def spawn_hub_process(addr: bytes | str, http_sockets: list) -> None:
    """Fork the hub, then return in the parent. Call this before worker forks."""
    global _addr, _listen_sock, _close_inherited
    _addr = addr
    _close_inherited = True
    listen = _bind(addr)
    _listen_sock = listen
    wake_r, wake_w = os.pipe()
    pid = os.fork()
    if pid != 0:
        os.close(wake_r)
        logger.info("Chat hub fanout listening (pid %s)", pid)
        return
    os.close(wake_w)
    for http_sock in http_sockets:
        try:
            http_sock.close()
        except OSError:
            pass
    threading.Thread(
        target=_accept_loop, args=(listen,), name="aird-chat-hub", daemon=True
    ).start()
    try:
        while True:
            chunk = os.read(wake_r, 16)
            if not chunk:
                break
    finally:
        os._exit(0)


def start_listener(path: str) -> None:
    """In-process hub for tests."""
    global _addr, _listen_sock, _client_started
    stop_listener()
    _addr = path
    _client_started = False
    listen = _bind(path)
    _listen_sock = listen
    threading.Thread(
        target=_accept_loop, args=(listen,), name="aird-chat-hub", daemon=True
    ).start()


def stop_listener() -> None:
    """Close a test hub. The production hub exits with the service."""
    global _addr, _listen_sock, _pub_sock, _client_started, _close_inherited
    listen = _listen_sock
    _listen_sock = None
    _addr = None
    _client_started = False
    _close_inherited = False
    if listen is not None:
        try:
            listen.close()
        except OSError:
            pass
    with _clients_lock:
        leftover = list(_clients)
        _clients.clear()
    for sock in leftover:
        try:
            sock.close()
        except OSError:
            pass
    with _pub_lock:
        pub = _pub_sock
        _pub_sock = None
    if pub is not None:
        try:
            pub.close()
        except OSError:
            pass


def connect_if_started(on_frame, on_connect=None) -> None:
    """Each worker calls this once. No-op when the process is single-worker."""
    global _client_started, _listen_sock
    if _addr is None or _client_started:
        return
    _client_started = True
    if _close_inherited:
        inherited = _listen_sock
        _listen_sock = None
        if inherited is not None:
            try:
                inherited.close()
            except OSError:
                pass
    threading.Thread(
        target=_worker_loop,
        args=(_addr, on_frame, on_connect),
        name="aird-chat-hub-client",
        daemon=True,
    ).start()


def _worker_loop(addr, on_frame, on_connect) -> None:
    import time

    while True:
        conn = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            conn.connect(addr)
        except OSError:
            conn.close()
            time.sleep(0.2)
            continue
        with _pub_lock:
            global _pub_sock
            _pub_sock = conn
        try:
            if on_connect is not None:
                on_connect()
            for body in _read_frames(conn):
                try:
                    frame = json.loads(body)
                except json.JSONDecodeError:
                    continue
                if isinstance(frame, dict):
                    on_frame(frame)
        except Exception:
            logger.debug("chat hub worker link closed", exc_info=True)
        finally:
            with _pub_lock:
                if _pub_sock is conn:
                    _pub_sock = None
            try:
                conn.close()
            except OSError:
                pass
        time.sleep(0.2)


def publish(frame: dict) -> bool:
    raw = json.dumps(frame, separators=(",", ":")).encode()
    if len(raw) > _MAX_FRAME:
        logger.warning("chat hub frame dropped (%s bytes)", len(raw))
        return False
    packet = _HDR.pack(len(raw)) + raw
    with _pub_lock:
        sock = _pub_sock
        if sock is None:
            return False
        try:
            sock.sendall(packet)
            return True
        except OSError:
            logger.debug("chat hub publish failed", exc_info=True)
            return False
