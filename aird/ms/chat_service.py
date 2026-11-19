"""Direct-messages microservice: chat REST + /ws/chat (Tornado, port 8008)."""

from __future__ import annotations

import logging
import os

import tornado.httpserver
import tornado.ioloop
import tornado.netutil
import tornado.web

import aird.config as config
import aird.constants as constants
from aird.handlers.health_handler import HealthHandler
from aird.ms.common import require_mesh_configuration
from aird.plugins.calls import register_calls
from aird.plugins.chat import chat_library_available, register_chat

logger = logging.getLogger(__name__)

SERVICE = "aird-chat"


def _build_chat_application() -> tornado.web.Application:
    from aird.core.auth_secrets import resolve_cookie_secret
    from aird.main import _build_app_context, _init_database

    config.init_config()
    _init_database()
    if not chat_library_available():
        raise SystemExit("Chat requires: pip install 'aird[chat]'")
    cookie_secret, _ = resolve_cookie_secret(constants.ROOT_DIR)
    os.environ.setdefault("AIRD_COOKIE_SECRET", cookie_secret)
    app_context = _build_app_context()
    routes: list = [(r"/health", HealthHandler), (r"/healthz", HealthHandler)]
    register_chat(routes)
    register_calls(routes)
    if len(routes) <= 2:
        raise SystemExit("direct_messages feature or chat routes are not available")

    settings = {
        "cookie_secret": cookie_secret,
        "xsrf_cookies": True,
        "login_url": "/login",
        "admin_login_url": "/admin/login",
        "db_conn": constants.DB_CONN,
        "cloud_manager": constants.CLOUD_MANAGER,
        "websocket_ping_interval": 20,
        "websocket_ping_timeout": 40,
        "app_context": app_context,
        "services": app_context.services,
    }
    return tornado.web.Application(routes, **settings)


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    require_mesh_configuration(SERVICE)
    app = _build_chat_application()
    from aird.main import _connect_chat_hub_bus

    _connect_chat_hub_bus()
    host = os.environ.get("AIRD_CHAT_HOST", "127.0.0.1")
    port = int(os.environ.get("AIRD_CHAT_PORT", "8008"))
    sockets = tornado.netutil.bind_sockets(port, address=host)
    server = tornado.httpserver.HTTPServer(app)
    server.add_sockets(sockets)
    logger.info("%s on %s:%s", SERVICE, host, port)
    tornado.ioloop.IOLoop.current().start()


if __name__ == "__main__":
    main()