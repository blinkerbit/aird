"""Run existing Tornado RequestHandlers on a socketify (libuv) HTTP server."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import tornado.gen
import tornado.httputil
import tornado.web
from tornado.httputil import HTTPHeaders, HTTPServerRequest

logger = logging.getLogger(__name__)


class SocketifyTornadoConnection:
    """Minimal HTTPConnection that writes through a socketify response."""

    def __init__(self, res: Any):
        self.res = res
        self.stream = None
        self.context = object()
        self._finished = False
        self._close_callback = None
        self.no_keep_alive = True

    def set_close_callback(self, callback):
        self._close_callback = callback

    def write_headers(self, start_line, headers, chunk=None, callback=None):
        code = getattr(start_line, "code", 200)
        reason = getattr(start_line, "reason", None) or tornado.httputil.responses.get(
            code, "OK"
        )
        try:
            self.res.write_status(f"{int(code)} {reason}")
        except Exception:
            self.res.write_status(int(code))
        if headers is not None:
            for name, value in headers.get_all():
                # socketify owns Connection/transfer framing
                if name.lower() in ("transfer-encoding",):
                    continue
                try:
                    self.res.write_header(name, value)
                except Exception:
                    logger.debug("skip header %s", name, exc_info=True)
        if chunk:
            self.res.write(chunk)
        if callback:
            callback()
        return None

    def write(self, chunk, callback=None):
        if chunk:
            self.res.write(chunk)
        if callback:
            callback()
        return None

    def finish(self):
        if self._finished:
            return
        self._finished = True
        try:
            if not self.res.has_responded():
                self.res.end(b"")
        except Exception:
            logger.debug("socketify finish/end failed", exc_info=True)
        if self._close_callback:
            try:
                self._close_callback()
            except Exception:
                pass

    def close(self):
        self.finish()


def _hdr(headers, *names: str) -> str:
    """Case-tolerant header get (Tornado HTTPHeaders or plain dict)."""
    for name in names:
        try:
            val = headers.get(name)
        except Exception:
            val = None
        if val is None:
            continue
        if isinstance(val, (list, tuple)):
            val = val[0] if val else None
        if val is None:
            continue
        s = str(val).strip()
        if s:
            return s
    return ""


def _headers_from_socketify(req: Any) -> HTTPHeaders:
    headers = HTTPHeaders()
    # Prefer per-header API — more reliable than get_headers() shape across versions
    try:
        req.for_each_header(lambda k, v: headers.add(str(k), str(v)))
        if headers:
            return headers
    except Exception:
        pass
    raw = None
    try:
        raw = req.get_headers()
    except Exception:
        raw = None
    if isinstance(raw, dict):
        for key, value in raw.items():
            if value is None:
                continue
            headers.add(str(key), str(value))
    return headers


async def execute_tornado_handler(
    application: tornado.web.Application,
    res: Any,
    req: Any,
    *,
    body: bytes = b"",
) -> None:
    """Dispatch one HTTP request to the Tornado Application router."""
    try:
        req.preserve()
    except Exception:
        pass

    method = req.get_method() or "GET"
    uri = req.get_full_url() or req.get_url() or "/"
    remote = "0.0.0.0"
    try:
        remote = res.get_remote_address() or remote
    except Exception:
        pass

    conn = SocketifyTornadoConnection(res)
    headers = _headers_from_socketify(req)
    if body and "Content-Length" not in headers:
        headers["Content-Length"] = str(len(body))

    protocol = "https" if headers.get("X-Forwarded-Proto") == "https" else "http"
    try:
        # socketify SSL apps still report http unless we detect otherwise
        if getattr(application, "aird_ssl", False):
            protocol = "https"
    except Exception:
        pass

    treq = HTTPServerRequest(
        method=method,
        uri=uri,
        version="HTTP/1.1",
        headers=headers,
        body=body,
        connection=conn,
    )
    treq.remote_ip = remote.split(":")[0] if remote else "0.0.0.0"
    treq.protocol = protocol

    delegate = application.find_handler(treq)
    handler_class = delegate.handler_class
    try:
        import tornado.websocket

        if issubclass(handler_class, tornado.websocket.WebSocketHandler):
            # Native socketify app.ws() should own these. Serving them via the
            # HTTP bridge produces a broken dual Tornado/uWS 101 handshake.
            logger.error(
                "HTTP bridge hit Tornado WebSocketHandler for %s "
                "(native socketify WS route missing or not matched)",
                uri,
            )
            if not res.has_responded():
                res.write_status(404).end("WebSocket endpoint not available")
            return
    except Exception:
        pass
    handler = handler_class(
        application, treq, **(delegate.handler_kwargs or {})
    )
    transforms = [t(treq) for t in application.transforms]
    fut = tornado.gen.convert_yielded(
        handler._execute(transforms, *delegate.path_args, **delegate.path_kwargs)
    )
    try:
        await asyncio.ensure_future(fut)
    except Exception:
        logger.exception("Tornado handler failed for %s %s", method, uri)
        if not res.has_responded():
            res.write_status(500).end("Internal Server Error")
    finally:
        if not conn._finished and not res.has_responded():
            # Handler wrote nothing (e.g. early return) — close cleanly
            try:
                res.end(b"")
            except Exception:
                pass
