"""Run existing Tornado WebSocketHandlers on socketify (libuv) WebSockets."""

from __future__ import annotations

import asyncio
import inspect
import logging
import threading
from typing import Any, Callable
from urllib.parse import unquote

from socketify import OpCode
from tornado.httputil import HTTPServerRequest
from tornado.websocket import WebSocketClosedError

from aird.web.socketify_bridge import _headers_from_socketify

logger = logging.getLogger(__name__)


class _SocketifyWSConnection:
    """Stand-in for Tornado's WebSocketProtocol / ws_connection."""

    def __init__(self, ws, loop: asyncio.AbstractEventLoop):
        self._ws = ws
        self._loop = loop
        self._closed = False

    def _send_now(self, message, binary: bool) -> None:
        if self._closed:
            raise WebSocketClosedError()
        try:
            if binary:
                payload = message if isinstance(message, (bytes, bytearray)) else bytes(message)
                self._ws.send(payload, OpCode.BINARY)
            else:
                if isinstance(message, (bytes, bytearray)):
                    text = message.decode("utf-8", errors="replace")
                else:
                    text = str(message)
                self._ws.send(text, OpCode.TEXT)
        except Exception as exc:
            self._closed = True
            raise WebSocketClosedError() from exc

    def write_message(self, message, binary=False):
        if self._closed:
            raise WebSocketClosedError()
        try:
            running = asyncio.get_running_loop()
        except RuntimeError:
            running = None
        if running is self._loop:
            self._send_now(message, binary)
            return None
        done = threading.Event()
        err: list[BaseException] = []

        def _do():
            try:
                self._send_now(message, binary)
            except BaseException as exc:  # noqa: BLE001 — ferry to caller
                err.append(exc)
            finally:
                done.set()

        self._loop.call_soon_threadsafe(_do)
        if not done.wait(timeout=30):
            raise WebSocketClosedError()
        if err:
            raise err[0]
        return None

    def close(self, code=None, reason=None):
        if self._closed:
            return
        self._closed = True

        def _do():
            try:
                self._ws.close()
            except Exception:
                pass

        try:
            running = asyncio.get_running_loop()
        except RuntimeError:
            running = None
        if running is self._loop:
            _do()
        else:
            self._loop.call_soon_threadsafe(_do)

    @property
    def client_terminated(self):
        return self._closed


def _schedule(loop: asyncio.AbstractEventLoop, fn: Callable[[], Any]) -> None:
    try:
        running = asyncio.get_running_loop()
    except RuntimeError:
        running = None
    if running is loop:
        fn()
    else:
        loop.call_soon_threadsafe(fn)


def _maybe_awaitable(loop: asyncio.AbstractEventLoop, result) -> None:
    if result is None:
        return
    if inspect.isawaitable(result):
        loop.create_task(result)


def _build_request(tornado_app, req, res, *, default_uri: str) -> HTTPServerRequest:
    headers = _headers_from_socketify(req)
    uri = default_uri
    try:
        uri = req.get_full_url() or req.get_url() or default_uri
    except Exception:
        pass
    remote = "0.0.0.0"
    try:
        remote = (res.get_remote_address() or remote).split(":")[0]
    except Exception:
        pass
    protocol = "https" if getattr(tornado_app, "aird_ssl", False) else "http"

    class _DummyConn:
        def set_close_callback(self, callback):
            return None

        def close(self):
            return None

    treq = HTTPServerRequest(
        method="GET",
        uri=uri,
        version="HTTP/1.1",
        headers=headers,
        body=b"",
        connection=_DummyConn(),
    )
    treq.remote_ip = remote
    treq.protocol = protocol
    return treq


def register_tornado_websocket(
    app,
    tornado_app,
    path: str,
    handler_class,
    *,
    path_args_fn: Callable[[Any, str], tuple] | None = None,
    max_payload_length: int = 16 * 1024 * 1024,
    idle_timeout: int = 300,
) -> None:
    """Attach a Tornado ``WebSocketHandler`` subclass to a socketify WS route."""

    aio_loop: asyncio.AbstractEventLoop = app.loop.loop

    def upgrade(res, req, socket_context):
        try:
            req.preserve()
        except Exception:
            pass
        uri = path
        try:
            uri = req.get_full_url() or req.get_url() or path
        except Exception:
            pass
        treq = _build_request(tornado_app, req, res, default_uri=uri)
        path_args = ()
        if path_args_fn is not None:
            try:
                path_args = tuple(path_args_fn(req, uri) or ())
            except Exception:
                logger.debug("path_args_fn failed for %s", path, exc_info=True)
                path_args = ()
        res.upgrade(
            req.get_header("sec-websocket-key"),
            req.get_header("sec-websocket-protocol"),
            req.get_header("sec-websocket-extensions"),
            socket_context,
            {
                "treq": treq,
                "path_args": path_args,
                "handler": None,
                "uri": uri,
            },
        )

    def open_handler(ws):
        state = ws.get_user_data() or {}
        treq = state.get("treq")
        path_args = state.get("path_args") or ()
        if treq is None:
            ws.close()
            return
        try:
            handler = handler_class(tornado_app, treq)
        except Exception:
            logger.exception("Failed to construct %s", handler_class)
            ws.close()
            return
        conn = _SocketifyWSConnection(ws, aio_loop)
        handler.ws_connection = conn
        handler.close = lambda code=None, reason=None: conn.close(code, reason)  # type: ignore[method-assign]
        handler._schedule = lambda fn: _schedule(aio_loop, fn)  # type: ignore[attr-defined]

        def write_message(message, binary=False):
            conn.write_message(message, binary=binary)
            fut = aio_loop.create_future()
            fut.set_result(None)
            return fut

        handler.write_message = write_message  # type: ignore[method-assign]
        # Mutate upgrade dict in place — WebSocket has no set_user_data().
        state["handler"] = handler

        def _open():
            try:
                result = handler.open(*path_args)
                _maybe_awaitable(aio_loop, result)
            except Exception:
                logger.exception("%s.open failed", handler_class.__name__)
                try:
                    conn.close()
                except Exception:
                    pass

        _schedule(aio_loop, _open)

    def message_handler(ws, message, opcode):
        state = ws.get_user_data() or {}
        handler = state.get("handler") if isinstance(state, dict) else None
        if handler is None:
            return
        if opcode == OpCode.BINARY or isinstance(message, (bytes, bytearray, memoryview)):
            payload: Any = bytes(message)
        else:
            if isinstance(message, (bytes, bytearray)):
                payload = message.decode("utf-8", errors="replace")
            else:
                payload = str(message)

        def _msg():
            try:
                result = handler.on_message(payload)
                _maybe_awaitable(aio_loop, result)
            except WebSocketClosedError:
                pass
            except Exception:
                logger.exception("%s.on_message failed", handler_class.__name__)

        _schedule(aio_loop, _msg)

    def close_handler(ws, code, message):
        state = ws.get_user_data() if isinstance(ws.get_user_data(), dict) else {}
        if not isinstance(state, dict):
            return
        handler = state.get("handler")
        if handler is None:
            return
        state["handler"] = None

        def _close():
            try:
                if getattr(handler, "ws_connection", None) is not None:
                    handler.ws_connection._closed = True  # type: ignore[attr-defined]
                result = handler.on_close()
                _maybe_awaitable(aio_loop, result)
            except Exception:
                logger.debug("%s.on_close failed", handler_class.__name__, exc_info=True)

        _schedule(aio_loop, _close)

    app.ws(
        path,
        {
            "compression": 0,
            "max_payload_length": max_payload_length,
            "idle_timeout": idle_timeout,
            "upgrade": upgrade,
            "open": open_handler,
            "message": message_handler,
            "close": close_handler,
        },
    )


def stream_path_args(req, uri: str) -> tuple:
    """Extract ``/stream/<path>`` capture (query stripped)."""
    path = uri.split("?", 1)[0]
    prefix = "/stream/"
    if path.startswith(prefix):
        return (unquote(path[len(prefix) :]),)
    if path == "/stream":
        return ("",)
    return (unquote(path.lstrip("/")),)
