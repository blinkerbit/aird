"""Bulk PUT/GET for aird-cli over the main HTTP port (WebSocket / HTTP).

Browser and CLI both use :PORT — no separate bulk TCP listener required.
Optional raw TCP remains available only when AIRD_BULK_PORT is set on the server.
"""

from __future__ import annotations

import base64
import hashlib
import secrets
import socket
import struct
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import requests

from aird.core.bulk.protocol import STATUS_OK, pack_client_header

_STATUS_NAMES = {
    0: "ok",
    1: "generic error",
    2: "ticket error",
    3: "path error",
    4: "size mismatch",
    5: "quota exceeded",
    6: "auth error",
}

_WS_ACCEPT_SALT = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"


def _websocket_accept_digest(sec_websocket_key: str) -> str:
    """RFC 6455 Sec-WebSocket-Accept value (SHA-1 required by protocol, not security)."""
    digest = hashlib.sha1(
        (sec_websocket_key + _WS_ACCEPT_SALT).encode("ascii"),
        usedforsecurity=False,
    ).digest()
    return base64.b64encode(digest).decode("ascii")


def _xsrf_header(session: requests.Session) -> dict[str, str]:
    tok = session.cookies.get("_xsrf", "")
    return {"X-XSRFToken": tok} if tok else {}


def _mint_ticket(
    session: requests.Session,
    base_url: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    r = session.post(
        f"{base_url.rstrip('/')}/api/bulk/ticket",
        json=payload,
        headers={"Content-Type": "application/json", **_xsrf_header(session)},
        timeout=60,
    )
    if r.status_code >= 400:
        raise RuntimeError(f"Bulk ticket failed ({r.status_code}): {r.text}")
    return r.json()


def _check_status(status_byte: int) -> None:
    if status_byte == STATUS_OK:
        return
    name = _STATUS_NAMES.get(status_byte, f"code {status_byte}")
    raise RuntimeError(f"Bulk transfer failed: {name}")


def _cookie_header(session: requests.Session) -> str:
    return "; ".join(f"{c.name}={c.value}" for c in session.cookies)


def _ws_url(base_url: str) -> tuple[str, int, str, bool]:
    parsed = urlparse(base_url)
    host = parsed.hostname or "127.0.0.1"
    tls = parsed.scheme == "https"
    port = parsed.port or (443 if tls else 80)
    return host, port, "/ws/bulk", tls


def _origin_for(base_url: str) -> str:
    parsed = urlparse(base_url)
    scheme = "https" if parsed.scheme == "https" else "http"
    host = parsed.hostname or "127.0.0.1"
    port = parsed.port
    if port and not (
        (scheme == "http" and port == 80) or (scheme == "https" and port == 443)
    ):
        return f"{scheme}://{host}:{port}"
    return f"{scheme}://{host}"


class _WsBinaryClient:
    """Minimal masked WebSocket client for binary bulk frames (stdlib only)."""

    def __init__(self, sock: socket.socket) -> None:
        self._sock = sock
        self._buf = bytearray()

    @classmethod
    def connect(
        cls,
        host: str,
        port: int,
        path: str,
        *,
        origin: str,
        cookie: str = "",
        timeout: float = 30,
    ) -> "_WsBinaryClient":
        sock = socket.create_connection((host, port), timeout=timeout)
        try:
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            key = base64.b64encode(secrets.token_bytes(16)).decode("ascii")
            req = (
                f"GET {path} HTTP/1.1\r\n"
                f"Host: {host}:{port}\r\n"
                "Upgrade: websocket\r\n"
                "Connection: Upgrade\r\n"
                f"Sec-WebSocket-Key: {key}\r\n"
                "Sec-WebSocket-Version: 13\r\n"
                f"Origin: {origin}\r\n"
            )
            if cookie:
                req += f"Cookie: {cookie}\r\n"
            req += "\r\n"
            sock.sendall(req.encode("ascii"))
            header = b""
            while b"\r\n\r\n" not in header:
                chunk = sock.recv(4096)
                if not chunk:
                    raise ConnectionError("WebSocket handshake closed early")
                header += chunk
                if len(header) > 64 * 1024:
                    raise ConnectionError("WebSocket handshake too large")
            status_line, _, _rest = header.partition(b"\r\n")
            if b"101" not in status_line:
                raise ConnectionError(
                    f"WebSocket upgrade failed: {status_line.decode('latin1', 'replace')}"
                )
            # RFC 6455 mandates SHA-1 for the Sec-WebSocket-Accept handshake value.
            expected = _websocket_accept_digest(key)
            if expected.encode("ascii") not in header:
                raise ConnectionError("WebSocket accept key mismatch")
            leftover = header.split(b"\r\n\r\n", 1)[1]
            client = cls(sock)
            if leftover:
                client._buf.extend(leftover)
            return client
        except Exception:
            sock.close()
            raise

    def _send_frame(self, opcode: int, payload: bytes) -> None:
        mask = secrets.token_bytes(4)
        plen = len(payload)
        header = bytes([0x80 | (opcode & 0x0F)])
        if plen < 126:
            header += bytes([0x80 | plen])
        elif plen < 65536:
            header += bytes([0x80 | 126]) + struct.pack(">H", plen)
        else:
            header += bytes([0x80 | 127]) + struct.pack(">Q", plen)
        header += mask
        masked = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
        self._sock.sendall(header + masked)

    def send_binary(self, data: bytes) -> None:
        # Split large payloads into frames (server reassembles via session.feed).
        view = memoryview(data)
        max_frame = 1024 * 1024
        while view:
            chunk = view[:max_frame].tobytes()
            view = view[max_frame:]
            self._send_frame(0x2, chunk)

    def _recv_exact(self, n: int) -> bytes:
        while len(self._buf) < n:
            chunk = self._sock.recv(65536)
            if not chunk:
                raise ConnectionError("unexpected EOF on WebSocket")
            self._buf.extend(chunk)
        out = bytes(self._buf[:n])
        del self._buf[:n]
        return out

    def _ws_extended_payload_length(self, plen: int) -> int:
        if plen == 126:
            (plen,) = struct.unpack(">H", self._recv_exact(2))
        elif plen == 127:
            (plen,) = struct.unpack(">Q", self._recv_exact(8))
        return plen

    @staticmethod
    def _ws_unmask_payload(payload: bytearray, mask: bytes) -> bytes:
        if mask:
            for i in range(len(payload)):
                payload[i] ^= mask[i % 4]
        return bytes(payload)

    def recv_binary(self) -> bytes:
        while True:
            b0, b1 = self._recv_exact(2)
            opcode = b0 & 0x0F
            masked = bool(b1 & 0x80)
            plen = self._ws_extended_payload_length(b1 & 0x7F)
            mask = self._recv_exact(4) if masked else b""
            payload = self._ws_unmask_payload(
                bytearray(self._recv_exact(plen)), mask
            )
            if opcode == 0x8:
                raise ConnectionError("WebSocket closed by server")
            if opcode == 0x9:  # ping
                self._send_frame(0xA, payload)
                continue
            if opcode in (0x1, 0x2, 0x0):
                return payload

    def close(self) -> None:
        try:
            self._send_frame(0x8, b"")
        except OSError:
            pass
        try:
            self._sock.close()
        except OSError:
            pass


def _tcp_host_port(base_url: str, ticket_resp: dict[str, Any]) -> tuple[str, int] | None:
    port = int(ticket_resp.get("port") or 0)
    if port <= 0:
        return None
    parsed = urlparse(base_url)
    host = parsed.hostname or "127.0.0.1"
    return host, port


def _put_via_tcp(
    host: str,
    port: int,
    ticket: bytes,
    path: Path,
    chunk_size: int,
) -> None:
    sock = socket.create_connection((host, port), timeout=30)
    try:
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        sock.sendall(pack_client_header(ticket))
        with path.open("rb") as src:
            while True:
                data = src.read(chunk_size)
                if not data:
                    break
                sock.sendall(data)
        status = _read_exact(sock, 1)[0]
        _check_status(status)
    finally:
        sock.close()


def _read_exact(sock: socket.socket, nbytes: int) -> bytes:
    parts: list[bytes] = []
    remaining = nbytes
    while remaining > 0:
        chunk = sock.recv(remaining)
        if not chunk:
            raise ConnectionError("unexpected EOF")
        parts.append(chunk)
        remaining -= len(chunk)
    return b"".join(parts)


def _put_via_ws(
    session: requests.Session,
    base_url: str,
    ticket: bytes,
    path: Path,
    chunk_size: int,
) -> None:
    host, port, ws_path, tls = _ws_url(base_url)
    if tls:
        raise RuntimeError(
            "Bulk WebSocket over TLS is not supported by aird-cli yet; "
            "use plain http:// on LAN/WireGuard, or set AIRD_BULK_PORT for raw TCP."
        )
    ws = _WsBinaryClient.connect(
        host,
        port,
        ws_path,
        origin=_origin_for(base_url),
        cookie=_cookie_header(session),
    )
    try:
        ws.send_binary(pack_client_header(ticket))
        with path.open("rb") as src:
            while True:
                data = src.read(chunk_size)
                if not data:
                    break
                ws.send_binary(data)
        status_payload = ws.recv_binary()
        if not status_payload:
            raise ConnectionError("empty bulk status frame")
        _check_status(status_payload[0])
    finally:
        ws.close()


def bulk_put_file(
    session: requests.Session,
    base_url: str,
    local_path: Path,
    remote_dir: str = "",
    *,
    filename: str | None = None,
    chunk_size: int = 8 * 1024 * 1024,
) -> None:
    path = Path(local_path)
    if not path.is_file():
        raise FileNotFoundError(path)
    fname = filename or path.name
    size = path.stat().st_size
    ticket_resp = _mint_ticket(
        session,
        base_url,
        {
            "op": "PUT",
            "upload_dir": remote_dir.strip("/"),
            "filename": fname,
            "size": size,
        },
    )
    ticket = ticket_resp["ticket"].encode("utf-8")
    tcp = _tcp_host_port(base_url, ticket_resp)
    if tcp:
        # Only when server set AIRD_BULK_PORT and advertised it.
        _put_via_tcp(tcp[0], tcp[1], ticket, path, chunk_size)
        return
    _put_via_ws(session, base_url, ticket, path, chunk_size)


def bulk_get_file(
    session: requests.Session,
    base_url: str,
    relpath: str,
    local_path: Path,
    *,
    chunk_size: int = 8 * 1024 * 1024,
) -> int:
    """Download via HTTP on the main server port (same host:port as the UI)."""
    relpath = relpath.strip().strip("/")
    url = f"{base_url.rstrip('/')}/files/{relpath}?download=1"
    with session.get(url, stream=True, timeout=600) as resp:
        if resp.status_code >= 400:
            raise RuntimeError(f"Download failed ({resp.status_code})")
        local_path = Path(local_path)
        local_path.parent.mkdir(parents=True, exist_ok=True)
        written = 0
        with local_path.open("wb") as out:
            for chunk in resp.iter_content(chunk_size=chunk_size):
                if chunk:
                    out.write(chunk)
                    written += len(chunk)
    return written
