"""HTTP/1 detach upload delegate — Rust socket→disk for stream uploads."""

from __future__ import annotations

import asyncio
import logging
import os
import socket
import ssl
import sys
from typing import Any, Awaitable, Optional

import tornado.httputil
from tornado.gen import Future
from tornado.web import _HandlerDelegate

from aird.core.transfer_native import (
    native_available,
    new_cancel_flag,
    tune_transfer_stream,
    zcopy_pump_detached,
)
from aird.handlers.file_op_handlers import UploadHandler, _remove_staged_upload_temp

logger = logging.getLogger(__name__)


def native_detach_eligible(
    request: tornado.httputil.HTTPServerRequest,
    headers: tornado.httputil.HTTPHeaders,
) -> bool:
    if request.method != "POST":
        return False
    if sys.platform == "win32" or not native_available():
        return False
    te = headers.get("Transfer-Encoding")
    if te:
        return False
    raw = headers.get("Content-Length")
    if not raw:
        return False
    try:
        return int(raw) > 0
    except (TypeError, ValueError):
        return False


def _blocking_socket_sendall(sock: socket.socket, data: bytes) -> None:
    was_blocking = sock.getblocking()
    try:
        sock.setblocking(True)
        sock.sendall(data)
    finally:
        sock.setblocking(was_blocking)


async def _send_on_stream(stream, payload: bytes) -> None:
    sock = getattr(stream, "socket", None)
    if sock is not None and isinstance(sock, ssl.SSLSocket):
        await stream.write(payload)
    elif sock is not None:
        await asyncio.to_thread(_blocking_socket_sendall, sock, payload)
    else:
        await stream.write(payload)


async def write_plain_response(stream, status: int, message: str) -> None:
    body = message.encode("utf-8")
    reason = tornado.httputil.responses.get(status, "Unknown")
    header_block = (
        f"HTTP/1.1 {status} {reason}\r\n"
        "Content-Type: text/plain; charset=UTF-8\r\n"
        f"Content-Length: {len(body)}\r\n"
        "Connection: close\r\n"
        "X-Content-Type-Options: nosniff\r\n"
        "\r\n"
    ).encode("ascii")
    try:
        await _send_on_stream(stream, header_block + body)
    except Exception:
        logger.exception("detach upload response write failed")
    finally:
        try:
            stream.close()
        except Exception:
            logger.debug("detach upload stream close failed", exc_info=True)


async def _pump_body(
    stream: Any,
    file_fd: int,
    content_length: int,
    cancel: Any,
) -> int:
    return await asyncio.wait_for(
        zcopy_pump_detached(stream, file_fd, content_length, cancel),
        timeout=max(600.0, content_length / (256 * 1024) + 120.0),
    )


class DetachUploadDelegate(_HandlerDelegate):
    """Delegate that detaches HTTP connections during headers_received for native uploads."""

    def headers_received(
        self,
        start_line: tornado.httputil.RequestStartLine
        | tornado.httputil.ResponseStartLine,
        headers: tornado.httputil.HTTPHeaders,
    ) -> Optional[Awaitable[None]]:
        if not self.stream_request_body or not native_detach_eligible(
            self.request, headers
        ):
            return super().headers_received(start_line, headers)
        if self.handler_class is UploadHandler and self.request.method == "POST":
            return self._detach_stream_upload(headers)
        return super().headers_received(start_line, headers)

    async def _detach_stream_upload(self, headers: tornado.httputil.HTTPHeaders) -> None:
        self.request.headers = headers
        handler = UploadHandler(self.application, self.request, **self.handler_kwargs)
        handler._init_upload_state()
        reject = handler.validate_upload_preflight(headers)
        if reject is not None:
            self.request._upload_preflight_reject = reject  # noqa: SLF001
            self.request._body_future = Future()
            prepared = self.execute()
            if prepared is not None:
                await prepared
            return
        await self._detach_recv_and_respond(
            handler,
            headers,
            file_opener=handler.open_upload_staging_fd,
            on_complete=handler.build_upload_result,
            response_writer=write_plain_response,
            cleanup_temp=lambda h: _remove_staged_upload_temp(
                getattr(h, "_temp_path", None)
            ),
            log_label="stream upload",
        )

    async def _detach_recv_and_respond(
        self,
        handler: Any,
        headers: tornado.httputil.HTTPHeaders,
        *,
        file_opener,
        on_complete,
        response_writer,
        cleanup_temp,
        log_label: str,
    ) -> None:
        conn = self.request.connection
        stream = getattr(conn, "stream", None)
        if stream is None:
            self.request._body_future = Future()
            prepared = self.execute()
            if prepared is not None:
                await prepared
            return

        try:
            content_length = int(headers["Content-Length"])
        except (KeyError, TypeError, ValueError):
            self.request._upload_preflight_reject = (400, "Invalid Content-Length")
            self.request._body_future = Future()
            prepared = self.execute()
            if prepared is not None:
                await prepared
            return

        file_fd: int | None = None
        detached = None
        try:
            if headers.get("Expect") == "100-continue":
                await stream.write(b"HTTP/1.1 100 Continue\r\n\r\n")
            tune_transfer_stream(stream)
            file_fd = file_opener()
            detached = conn.detach()
            cancel = new_cancel_flag()
            nbytes = await _pump_body(detached, file_fd, content_length, cancel)
            try:
                os.close(file_fd)
            except OSError:
                pass
            file_fd = None
            handler._bytes_received = nbytes
            handler._detach_upload_complete = True
            status, message = await on_complete()
            logger.info(
                "Detach %s finished: %d bytes status=%s",
                log_label,
                nbytes,
                status,
            )
            await response_writer(detached, status, message)
            detached = None
        except asyncio.TimeoutError:
            logger.error("Native detach %s timed out during body read", log_label)
            cleanup_temp(handler)
            if detached is not None:
                await response_writer(detached, 504, "Upload read timed out")
        except Exception:
            logger.exception("Native detach %s failed", log_label)
            cleanup_temp(handler)
            if detached is not None:
                from aird.constants.file_ops import UPLOAD_SAVE_FAILED

                await response_writer(detached, 500, UPLOAD_SAVE_FAILED)
        finally:
            if file_fd is not None and file_fd >= 0:
                try:
                    os.close(file_fd)
                except OSError:
                    logger.debug("detach staging fd close failed", exc_info=True)
