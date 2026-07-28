"""Freethreaded bulk PUT/GET engine (runs off the Tornado IOLoop)."""

from __future__ import annotations

import logging
import os
import queue
import socket
import sys
import tempfile
from typing import Protocol

from aird.core.bulk.ip import client_ips_match
from aird.core.bulk.paths import (
    resolve_download_path,
    user_root_for_username,
    validate_upload_path,
)
from aird.core.bulk.protocol import (
    BUFFER_POOL_SIZE,
    BUFFER_SIZE,
    HEADER_PREFIX_LEN,
    STATUS_ERR_GENERIC,
    STATUS_ERR_PATH,
    STATUS_ERR_QUOTA,
    STATUS_ERR_SIZE,
    STATUS_ERR_TICKET,
    STATUS_OK,
    unpack_client_header,
)
from aird.core.bulk.ticket import BulkTicketClaims, verify_bulk_ticket
from aird.core.file_send import sendfile_available
from aird.handlers.file_op_handlers import finalize_upload_to_disk
from aird.utils.util import is_feature_enabled

logger = logging.getLogger(__name__)


class BulkTransport(Protocol):
    def recv(self, nbytes: int) -> bytes: ...

    def recv_into(self, buf: memoryview) -> int: ...

    def sendall(self, data: bytes) -> None: ...

    def close(self) -> None: ...


class _SocketTransport:
    def __init__(self, sock: socket.socket) -> None:
        self._sock = sock

    def recv(self, nbytes: int) -> bytes:
        return self._sock.recv(nbytes)

    def recv_into(self, buf: memoryview) -> int:
        return self._sock.recv_into(buf)

    def sendall(self, data: bytes) -> None:
        self._sock.sendall(data)

    def close(self) -> None:
        try:
            self._sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        try:
            self._sock.close()
        except OSError:
            pass

    @property
    def raw_socket(self) -> socket.socket:
        return self._sock


class _BufferPool:
    def __init__(self, bufsize: int, count: int) -> None:
        self._bufs = [bytearray(bufsize) for _ in range(count)]
        self._free: queue.Queue[bytearray] = queue.Queue()
        for buf in self._bufs:
            self._free.put(buf)

    def acquire(self) -> bytearray:
        return self._free.get()

    def release(self, buf: bytearray) -> None:
        self._free.put(buf)


class BulkEngine:
    """Execute PUT/GET sessions on a dedicated thread."""

    def __init__(self, app_context, ticket_secret: str) -> None:
        self._app_context = app_context
        self._ticket_secret = ticket_secret
        self._pool = _BufferPool(BUFFER_SIZE, BUFFER_POOL_SIZE)

    def handle_tcp_socket(self, sock: socket.socket, peer: str | None = None) -> None:
        transport = _SocketTransport(sock)
        try:
            status = self.execute_session(transport, peer)
            transport.sendall(bytes([status]))
        except OSError:
            pass
        finally:
            transport.close()

    def execute_session(self, transport: BulkTransport, peer: str | None = None) -> int:
        try:
            header = self._read_header(transport)
            ticket, _ = unpack_client_header(header)
            claims = verify_bulk_ticket(self._ticket_secret, ticket.decode("utf-8"))
            if claims.remote_ip and peer and not client_ips_match(claims.remote_ip, peer):
                raise ValueError("ticket IP mismatch")
            if claims.op == "PUT":
                return self._do_put(transport, claims, peer)
            return self._do_get(transport, claims)
        except ValueError as exc:
            logger.info("Bulk session rejected: %s", exc)
            return STATUS_ERR_TICKET
        except ConnectionError:
            return STATUS_ERR_GENERIC
        except Exception:
            logger.exception("Bulk session failed")
            return STATUS_ERR_GENERIC

    def _read_header(self, transport: BulkTransport) -> bytes:
        import struct

        from aird.core.bulk.protocol import HEADER_PREFIX_LEN, MAGIC

        buf = bytearray()
        while len(buf) < HEADER_PREFIX_LEN:
            chunk = transport.recv(HEADER_PREFIX_LEN - len(buf))
            if not chunk:
                raise ConnectionError("EOF before header")
            buf.extend(chunk)
        if bytes(buf[: len(MAGIC)]) != MAGIC:
            raise ConnectionError("bad magic")
        (ticket_len,) = struct.unpack(">I", buf[len(MAGIC) + 1 : HEADER_PREFIX_LEN])
        total = HEADER_PREFIX_LEN + ticket_len
        while len(buf) < total:
            chunk = transport.recv(total - len(buf))
            if not chunk:
                raise ConnectionError("EOF during header")
            buf.extend(chunk)
        header = bytes(buf[:total])
        unpack_client_header(header)
        return header

    def _read_exact(self, transport: BulkTransport, nbytes: int) -> bytes:
        parts: list[bytes] = []
        remaining = nbytes
        while remaining > 0:
            chunk = transport.recv(remaining)
            if not chunk:
                raise ConnectionError("unexpected EOF")
            parts.append(chunk)
            remaining -= len(chunk)
        return b"".join(parts)

    def _recv_payload(self, transport: BulkTransport, fd: int, size: int) -> None:
        received = 0
        buf = self._pool.acquire()
        try:
            while received < size:
                want = min(len(buf), size - received)
                mv = memoryview(buf)[:want]
                n = transport.recv_into(mv)
                if n <= 0:
                    raise ConnectionError("unexpected EOF during payload")
                os.write(fd, mv[:n])
                received += n
        finally:
            self._pool.release(buf)

    def _do_put(
        self, transport: BulkTransport, claims: BulkTicketClaims, peer: str | None
    ) -> int:
        if not is_feature_enabled("file_upload", True):
            return STATUS_ERR_GENERIC
        user_root = user_root_for_username(claims.username)
        final_path, err = validate_upload_path(
            claims.upload_dir, claims.filename, user_root
        )
        if err is not None:
            return STATUS_ERR_PATH
        dest_dir = os.path.dirname(final_path) or user_root
        os.makedirs(dest_dir, exist_ok=True)
        fd, temp_path = tempfile.mkstemp(prefix=".aird_bulk_", dir=dest_dir)
        try:
            self._recv_payload(transport, fd, claims.size)
            os.close(fd)
            fd = -1
            ok, status_code, _msg = finalize_upload_to_disk(
                upload_dir=claims.upload_dir,
                filename=claims.filename,
                temp_path=temp_path,
                user_root=user_root,
                username=claims.username,
                db_conn=self._app_context.db_conn,
                quota_service=self._app_context.quota_service,
                audit_service=self._app_context.audit_service,
                remote_ip=peer.split(":")[0] if peer else None,
                upload_bytes=claims.size,
            )
            if not ok:
                if status_code == 413:
                    return STATUS_ERR_QUOTA
                return STATUS_ERR_PATH
            return STATUS_OK
        finally:
            if fd >= 0:
                try:
                    os.close(fd)
                except OSError:
                    pass
            if os.path.exists(temp_path):
                try:
                    os.unlink(temp_path)
                except OSError:
                    pass

    def _do_get(self, transport: BulkTransport, claims: BulkTicketClaims) -> int:
        user_root = user_root_for_username(claims.username)
        abspath, err = resolve_download_path(claims.relpath, user_root)
        if err or not abspath:
            return STATUS_ERR_PATH
        try:
            file_size = os.path.getsize(abspath)
        except OSError:
            return STATUS_ERR_PATH
        if file_size != claims.size:
            return STATUS_ERR_SIZE
        self._send_file(transport, abspath, file_size)
        return STATUS_OK

    def _send_file(self, transport: BulkTransport, path: str, size: int) -> None:
        raw_sock = getattr(transport, "raw_socket", None)
        if raw_sock is not None and sendfile_available() and sys.platform.startswith("linux"):
            try:
                sock_fd = raw_sock.fileno()
                sent = 0
                with open(path, "rb") as src:
                    in_fd = src.fileno()
                    while sent < size:
                        chunk = min(BUFFER_SIZE, size - sent)
                        n = os.sendfile(sock_fd, in_fd, sent, chunk)
                        if n <= 0:
                            break
                        sent += n
                if sent >= size:
                    return
            except OSError:
                logger.debug("bulk sendfile fallback", exc_info=True)

        buf = self._pool.acquire()
        try:
            with open(path, "rb") as src:
                remaining = size
                while remaining > 0:
                    n = src.readinto(memoryview(buf)[: min(len(buf), remaining)])
                    if n <= 0:
                        raise ConnectionError("short file read during bulk GET")
                    transport.sendall(bytes(memoryview(buf)[:n]))
                    remaining -= n
        finally:
            self._pool.release(buf)
