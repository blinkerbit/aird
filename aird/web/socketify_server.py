"""libuv/socketify HTTP+WS server entry — replaces Tornado HTTPServer."""

from __future__ import annotations

import json
import logging
import os
import re
from typing import Any
from urllib.parse import unquote

from socketify import App, AppOptions, OpCode

import aird.constants as constants
from aird.core.transfer_engine import (
    _plain_end,
    stream_download_chunks,
    stream_upload_on_data,
)
from aird.handlers.file_op_handlers import _remove_staged_upload_temp
from aird.web.socketify_bridge import execute_tornado_handler

logger = logging.getLogger(__name__)

_DOWNLOAD_RE = re.compile(r"^/files/(.*)$")


def _cookie_header(req: Any) -> str:
    try:
        return req.get_header("cookie") or ""
    except Exception:
        return ""


def _parse_cookie(header: str, name: str) -> str | None:
    if not header:
        return None
    for part in header.split(";"):
        part = part.strip()
        if part.startswith(name + "="):
            return part[len(name) + 1 :]
    return None


def _tornado_secure_cookie(application, req: Any, name: str) -> bytes | None:
    """Decode Tornado signed cookie without a full RequestHandler."""
    raw = _parse_cookie(_cookie_header(req), name)
    if not raw:
        return None
    secret = application.settings.get("cookie_secret")
    if not secret:
        return None
    try:
        from tornado.web import decode_signed_value

        return decode_signed_value(secret, name, raw)
    except Exception:
        return None


def _authenticated_user(application, req: Any):
    from aird.handlers.base_handler import authenticate_handler

    class _Mini:
        def __init__(self):
            self.application = application
            self.request = type("R", (), {"cookies": {}, "headers": {}})()
            self.cookies = {}

        def get_secure_cookie(self, name, value=None):
            return _tornado_secure_cookie(application, req, name)

        def get_cookie(self, name, default=None):
            return _parse_cookie(_cookie_header(req), name) or default

    return authenticate_handler(_Mini())


def create_socketify_app(tornado_app, *, ssl_options: dict | None = None) -> App:
    """Build a socketify App that serves *tornado_app* routes on libuv."""
    if ssl_options:
        app = App(
            AppOptions(
                key_file_name=ssl_options.get("keyfile"),
                cert_file_name=ssl_options.get("certfile"),
                passphrase=ssl_options.get("password"),
            )
        )
        tornado_app.aird_ssl = True
    else:
        app = App()
        tornado_app.aird_ssl = False

    app.aird_tornado = tornado_app
    static_path = tornado_app.settings.get("static_path")
    if static_path and os.path.isdir(static_path):
        app.static("/static", static_path)

    async def handle_upload(res, req):
        req.preserve()
        tornado_app = app.aird_tornado
        from aird.core import upload_sessions
        from aird.core.transfer_engine import stream_upload_range_on_data
        from aird.handlers.base_handler import get_user_root
        from aird.handlers.file_op_handlers import UploadHandler
        from aird.web.socketify_bridge import (
            SocketifyTornadoConnection,
            _hdr,
            _headers_from_socketify,
        )
        from tornado.httputil import HTTPServerRequest

        headers = _headers_from_socketify(req)
        conn = SocketifyTornadoConnection(res)
        treq = HTTPServerRequest(
            method="POST",
            uri=req.get_full_url() or "/upload",
            headers=headers,
            body=b"",
            connection=conn,
        )
        treq.remote_ip = (res.get_remote_address() or "0.0.0.0").split(":")[0]
        treq.protocol = (
            "https" if getattr(tornado_app, "aird_ssl", False) else "http"
        )
        handler = UploadHandler(tornado_app, treq)
        handler._init_upload_state()
        reject = handler.validate_upload_preflight(headers)
        if reject is not None:
            _plain_end(res, reject[0], reject[1])
            return

        user_root = get_user_root(handler)
        # Prefer socketify req.get_header (lowercase) — get_headers() can omit customs.
        session_id = (
            _hdr(headers, "X-Upload-Session", "x-upload-session")
            or (req.get_header("x-upload-session") or "").strip()
        )
        complete = _hdr(
            headers, "X-Upload-Complete", "x-upload-complete"
        ).lower() in ("1", "true", "yes") or (
            (req.get_header("x-upload-complete") or "").strip().lower()
            in ("1", "true", "yes")
        )

        # Finalize a parallel session (empty body).
        if complete and upload_sessions.valid_session_id(session_id):
            sess = upload_sessions.pop_session(user_root, session_id)
            if sess is None:
                _plain_end(res, 404, "Upload session not found")
                return
            upload_sessions.close_session_fd(sess)
            try:
                size = os.path.getsize(sess.temp_path)
            except OSError:
                size = 0
            if size != sess.total:
                try:
                    if os.path.exists(sess.temp_path):
                        os.remove(sess.temp_path)
                except OSError:
                    pass
                _plain_end(res, 499, "Upload incomplete")
                return
            handler.upload_dir = sess.upload_dir
            handler.filename = sess.filename
            handler._temp_path = sess.temp_path
            handler._bytes_received = sess.total
            handler._expected_bytes = sess.total
            handler._moved = False
            status, message = await handler.build_upload_result()
            if not handler._moved:
                _remove_staged_upload_temp(sess.temp_path)
            _plain_end(res, status, message)
            return

        try:
            expected = int(headers.get("Content-Length") or 0)
        except ValueError:
            expected = 0
        if expected <= 0 and not complete:
            _plain_end(res, 400, "Content-Length required")
            return

        # Parallel ranged chunk into a session staging file.
        offset_raw = _hdr(headers, "X-Upload-Offset", "x-upload-offset") or (
            req.get_header("x-upload-offset") or ""
        )
        total_raw = _hdr(headers, "X-Upload-Total", "x-upload-total") or (
            req.get_header("x-upload-total") or ""
        )
        if (
            upload_sessions.valid_session_id(session_id)
            and offset_raw != ""
            and total_raw != ""
        ):
            try:
                offset = int(offset_raw)
                total = int(total_raw)
            except ValueError:
                _plain_end(res, 400, "Invalid upload range")
                return
            try:
                sess = upload_sessions.open_or_get_session(
                    session_id=session_id,
                    user_root=user_root,
                    upload_dir=handler.upload_dir or "",
                    filename=handler.filename,
                    total=total,
                )
            except ValueError as exc:
                _plain_end(res, 400, str(exc) or "Bad upload session")
                return

            async def _chunk_done(nbytes: int):
                _plain_end(res, 200, f"OK {nbytes}")

            await stream_upload_range_on_data(
                res,
                write_chunk=lambda abs_off, data: upload_sessions.write_at_offset(
                    sess, abs_off, data
                ),
                file_offset=offset,
                expected_bytes=expected,
                on_complete=_chunk_done,
                on_error=lambda status, message: _plain_end(res, status, message),
            )
            return

        async def _finalize(temp_path: str, nbytes: int):
            handler._temp_path = temp_path
            handler._bytes_received = nbytes
            handler._expected_bytes = expected
            handler._moved = False
            status, message = await handler.build_upload_result()
            if not handler._moved:
                _remove_staged_upload_temp(temp_path)
            _plain_end(res, status, message)

        stage_dir = user_root if os.path.isdir(user_root) else constants.ROOT_DIR
        await stream_upload_on_data(
            res,
            dest_dir=stage_dir,
            expected_bytes=expected,
            on_complete=_finalize,
            on_error=lambda status, message: _plain_end(res, status, message),
        )

    async def handle_download(res, req, rel_path: str):
        req.preserve()
        tornado_app = app.aird_tornado
        from aird.handlers.base_handler import BaseHandler, get_user_root
        from aird.web.socketify_bridge import (
            SocketifyTornadoConnection,
            _headers_from_socketify,
        )
        from tornado.httputil import HTTPServerRequest

        headers = _headers_from_socketify(req)
        conn = SocketifyTornadoConnection(res)
        treq = HTTPServerRequest(
            method="GET",
            uri=req.get_full_url() or req.get_url() or "/",
            headers=headers,
            body=b"",
            connection=conn,
        )
        treq.remote_ip = (res.get_remote_address() or "0.0.0.0").split(":")[0]
        treq.protocol = "https" if getattr(tornado_app, "aird_ssl", False) else "http"
        handler = BaseHandler(tornado_app, treq)
        user = handler.get_current_user()
        if not user:
            res.write_status(403).end("Authentication required")
            return

        user_root = get_user_root(handler)
        abspath = os.path.realpath(os.path.join(user_root, unquote(rel_path)))
        if not abspath.startswith(os.path.realpath(user_root)):
            res.write_status(403).end("Access denied")
            return
        if not os.path.isfile(abspath):
            await _dispatch_http(res, req)
            return
        await stream_download_chunks(
            res,
            req,
            abspath,
            filename=os.path.basename(abspath),
            as_attachment=True,
        )

    async def _dispatch_http(res, req):
        method = (req.get_method() or "GET").upper()
        body = b""
        if method in ("POST", "PUT", "PATCH"):
            try:
                data = await res.get_data()
                body = data.getvalue() if data is not None else b""
            except Exception:
                logger.debug("body read failed", exc_info=True)
                body = b""
        await execute_tornado_handler(app.aird_tornado, res, req, body=body)

    async def dispatch(res, req):
        try:
            req.preserve()
        except Exception:
            pass
        path = req.get_url() or "/"
        method = (req.get_method() or "GET").upper()

        if path == "/upload" and method == "POST":
            await handle_upload(res, req)
            return

        if method == "GET" and req.get_query("download"):
            m = _DOWNLOAD_RE.match(path)
            if m:
                await handle_download(res, req, m.group(1))
                return

        await _dispatch_http(res, req)

    # Catch-all HTTP (static already registered). WebSockets are registered after;
    # uWS routes Upgrade to app.ws() when a matching WS path exists.
    for method in ("get", "post", "put", "patch", "delete", "options", "head"):
        getattr(app, method)("/*", dispatch)
    app.any("/", dispatch)

    _register_websockets(app, tornado_app)
    return app


def _ws_user_from_upgrade(application, req) -> Any:
    return _authenticated_user(application, req)


def _register_websockets(app: App, tornado_app) -> None:
    """Native socketify WS endpoints + Tornado handler bridges."""

    from aird.handlers.api_handlers import (
        FeatureFlagSocketHandler,
        RuntimeConfigSocketHandler,
        _runtime_config_from_settings,
    )
    from aird.utils.util import get_current_feature_flags

    def _ff_send(_cls=None):
        FeatureFlagSocketHandler.connection_manager.broadcast_message(
            json.dumps(get_current_feature_flags())
        )

    def _rt_send(_cls=None, runtime_config=None):
        payload = runtime_config or _runtime_config_from_settings(tornado_app.settings)
        RuntimeConfigSocketHandler.connection_manager.broadcast_message(
            json.dumps(payload)
        )

    FeatureFlagSocketHandler.send_updates = classmethod(lambda cls: _ff_send())
    RuntimeConfigSocketHandler.send_updates = classmethod(
        lambda cls, runtime_config=None: _rt_send(runtime_config=runtime_config)
    )

    def _ws_auth(application, req):
        from aird.handlers.base_handler import BaseHandler
        from aird.web.socketify_bridge import (
            SocketifyTornadoConnection,
            _headers_from_socketify,
        )
        from tornado.httputil import HTTPServerRequest

        headers = _headers_from_socketify(req)
        # No response object during upgrade — use a dummy connection
        class _Dummy:
            def write_headers(self, *a, **k):
                return None

            def write(self, *a, **k):
                return None

            def finish(self):
                return None

            def set_close_callback(self, cb):
                return None

        treq = HTTPServerRequest(
            method="GET",
            uri=req.get_full_url() or req.get_url() or "/ws/file-transfer",
            headers=headers,
            body=b"",
            connection=_Dummy(),
        )
        treq.remote_ip = "0.0.0.0"
        treq.protocol = "https" if getattr(application, "aird_ssl", False) else "http"
        return BaseHandler(application, treq).get_current_user()

    from aird.web.socketify_ws_transfer import register_file_transfer_ws
    from aird.web.socketify_ws_tornado import register_tornado_websocket, stream_path_args

    register_file_transfer_ws(app, tornado_app, auth_user_fn=_ws_auth)

    from aird.handlers.bulk_handlers import BulkWebSocketHandler
    from aird.handlers.p2p_handlers import P2PSignalingHandler
    from aird.handlers.abac_handlers import PolicyDecisionsWebSocket
    from aird.handlers.api_handlers import (
        FileStreamHandler,
        SuperSearchWebSocketHandler,
        FeatureFlagSocketHandler,
        RuntimeConfigSocketHandler,
        _runtime_config_from_settings,
    )

    register_tornado_websocket(
        app,
        tornado_app,
        "/features",
        FeatureFlagSocketHandler,
        max_payload_length=64 * 1024,
        idle_timeout=600,
    )
    register_tornado_websocket(
        app,
        tornado_app,
        "/runtime-config",
        RuntimeConfigSocketHandler,
        max_payload_length=256 * 1024,
        idle_timeout=600,
    )
    register_tornado_websocket(
        app,
        tornado_app,
        "/search/ws",
        SuperSearchWebSocketHandler,
        max_payload_length=64 * 1024,
        idle_timeout=180,
    )
    register_tornado_websocket(
        app,
        tornado_app,
        "/ws/bulk",
        BulkWebSocketHandler,
        max_payload_length=16 * 1024 * 1024,
        idle_timeout=600,
    )
    register_tornado_websocket(
        app,
        tornado_app,
        "/p2p/signal",
        P2PSignalingHandler,
        max_payload_length=1 * 1024 * 1024,
        idle_timeout=300,
    )
    register_tornado_websocket(
        app,
        tornado_app,
        "/ws/policy-decisions",
        PolicyDecisionsWebSocket,
        max_payload_length=256 * 1024,
        idle_timeout=300,
    )
    register_tornado_websocket(
        app,
        tornado_app,
        "/stream/*",
        FileStreamHandler,
        path_args_fn=stream_path_args,
        max_payload_length=16 * 1024 * 1024,
        idle_timeout=300,
    )


def run_socketify_app(
    tornado_app,
    *,
    host: str = "0.0.0.0",
    port: int = 8000,
    ssl_options: dict | None = None,
) -> None:
    """Block on socketify libuv loop (main thread)."""
    from aird.core.transfer_http_listener import start_native_upload_listener

    start_native_upload_listener(port)

    app = create_socketify_app(tornado_app, ssl_options=ssl_options)

    def _on_listen(config):
        if config:
            logger.info(
                "socketify/libuv listening on http%s://%s:%s",
                "s" if ssl_options else "",
                host,
                config.port,
            )

    listen_opts = port
    try:
        from socketify import AppListenOptions

        listen_opts = AppListenOptions(port=port, host=host)
    except Exception:
        pass

    app.listen(listen_opts, _on_listen)
    app.run()
