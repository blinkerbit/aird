"""HTTP ticket minting and WebSocket bulk upload bridge."""

from __future__ import annotations

import json
import logging

import tornado.ioloop
import tornado.web
import tornado.websocket

import aird.constants as constants_module
from aird.constants.input_limits import REL_PATH_MAX_LEN
from aird.core.bulk.ticket import (
    BULK_TICKET_TTL_SEC,
    BulkTicketClaims,
    default_expiry,
    mint_bulk_ticket,
)
from aird.core.bulk.tcp_server import get_bulk_tcp_port
from aird.core.bulk.ws_bridge import WsBulkSession
from aird.core.security import is_valid_websocket_origin
from aird.handlers.base_handler import (
    BaseHandler,
    ManagedWebSocketMixin,
    get_username_string_for_db,
)
from aird.handlers.constants import AUTH_REQUIRED
from aird.utils.util import WebSocketConnectionManager, is_feature_enabled

logger = logging.getLogger(__name__)

_TOKEN_ONLY_USERNAMES = {"token_user", "admin_token"}


def _bulk_ticket_secret(handler) -> str:
    secret = handler.settings.get("cookie_secret") or ""
    if not secret:
        raise tornado.web.HTTPError(500, "Server misconfigured")
    return secret


def _bulk_engine(handler):
    app_ctx = handler.settings.get("app_context")
    if app_ctx is None:
        raise tornado.web.HTTPError(500, "Server misconfigured")
    engine = handler.settings.get("bulk_engine")
    if engine is None:
        raise tornado.web.HTTPError(503, "Bulk transfer not enabled")
    return engine


class BulkTicketHandler(BaseHandler):
    """Mint short-lived HMAC tickets for bulk TCP/WS transfers."""

    @tornado.web.authenticated
    def post(self):
        if not is_feature_enabled("file_upload", True):
            raise tornado.web.HTTPError(403, "Upload disabled")
        try:
            data = json.loads(self.request.body.decode("utf-8", errors="replace") or "{}")
        except json.JSONDecodeError:
            raise tornado.web.HTTPError(400, "Invalid JSON")

        op = str(data.get("op", "")).upper()
        if op not in ("PUT", "GET"):
            raise tornado.web.HTTPError(400, "op must be PUT or GET")

        try:
            size = int(data.get("size", 0))
        except (TypeError, ValueError):
            raise tornado.web.HTTPError(400, "Invalid size")
        if size < 0 or size > constants_module.MAX_FILE_SIZE:
            raise tornado.web.HTTPError(400, "Invalid size")

        username = get_username_string_for_db(self)
        if not username or username in _TOKEN_ONLY_USERNAMES:
            raise tornado.web.HTTPError(403, AUTH_REQUIRED)

        if op == "PUT":
            if not self.has_modify_privileges():
                raise tornado.web.HTTPError(403, "Modify access required")
            upload_dir = str(data.get("upload_dir", "") or "")
            filename = str(data.get("filename", "") or "")
            if not filename:
                raise tornado.web.HTTPError(400, "filename required")
            if len(filename) > 255 or len(upload_dir) > REL_PATH_MAX_LEN:
                raise tornado.web.HTTPError(400, "path too long")
            claims = BulkTicketClaims(
                op="PUT",
                username=username,
                size=size,
                exp=default_expiry(),
                upload_dir=upload_dir,
                filename=filename,
                remote_ip=self.request.remote_ip,
            )
        else:
            relpath = str(data.get("relpath", "") or "").strip().strip("/")
            if not relpath or len(relpath) > REL_PATH_MAX_LEN:
                raise tornado.web.HTTPError(400, "relpath required")
            claims = BulkTicketClaims(
                op="GET",
                username=username,
                size=size,
                exp=default_expiry(),
                relpath=relpath,
                remote_ip=self.request.remote_ip,
            )

        ticket = mint_bulk_ticket(_bulk_ticket_secret(self), claims)
        port = get_bulk_tcp_port()
        self.set_header("Content-Type", "application/json")
        self.write(
            {
                "ticket": ticket,
                "port": port,
                "expires": claims.exp,
                "ttl": BULK_TICKET_TTL_SEC,
            }
        )


class BulkWebSocketHandler(ManagedWebSocketMixin, tornado.websocket.WebSocketHandler):
    """Browser bulk upload: binary frames → BulkEngine (PUT only)."""

    connection_manager = WebSocketConnectionManager(
        "bulk_transfer", default_max_connections=64, default_idle_timeout=600
    )

    def check_origin(self, origin: str) -> bool:
        return is_valid_websocket_origin(self, origin)

    def open(self, *args, **kwargs):
        if not self.get_current_user():
            self.close(4401, AUTH_REQUIRED)
            return
        if not self.register_connection():
            return
        if not is_feature_enabled("file_upload", True):
            self.close(4403, "Upload disabled")
            return
        self._session: WsBulkSession | None = None
        self._peer = self.request.remote_ip

    def on_message(self, message):
        if isinstance(message, str):
            self.close(4400, "Binary frames required")
            return
        if not message:
            return
        if self._session is None:
            engine = _bulk_engine(self)
            loop = tornado.ioloop.IOLoop.current()

            def on_complete(status: int) -> None:
                loop.add_callback(self._send_status, status)

            self._session = WsBulkSession(engine, on_complete)
            self._session.feed(message)
            self._session.start(self._peer)
            return
        if self._session:
            self._session.feed(message)

    def on_close(self) -> None:
        if self._session and self._session.started:
            self._session.finish_feed()
        super().on_close()

    def _send_status(self, status: int) -> None:
        try:
            if self.ws_connection:
                self.write_message(bytes([status]), binary=True)
        except tornado.websocket.WebSocketClosedError:
            pass
        finally:
            try:
                self.close()
            except Exception:
                pass
