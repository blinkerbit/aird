"""
Aird features microservice — Granian ASGI (Starlette).

Upload/download bodies stay on ``aird.ms.transfer`` (socketify).
"""

from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager

from starlette.applications import Starlette
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import PlainTextResponse
from starlette.routing import Mount
from starlette.staticfiles import StaticFiles

from aird.ms.common import mesh_secret_ok, require_mesh_configuration
from aird.ms.ctx import AppState
from aird.ms.render import static_dir, templates_dir
from aird.ms.routers.admin import routes as admin_routes
from aird.ms.routers.auth import routes as auth_routes
from aird.ms.routers.auth_verify import routes as auth_verify_routes
from aird.ms.routers.browse import routes as browse_routes
from aird.ms.routers.config_api import routes as config_routes
from aird.ms.routers.file_ops import routes as file_ops_routes
from aird.ms.routers.health import routes as health_routes
from aird.ms.routers.extra_api import routes as extra_api_routes
from aird.ms.routers.shares import routes as share_routes
from aird.ms.routers.ws import routes as ws_routes
from aird.ms.routers.ws_extra import routes as ws_extra_routes

logger = logging.getLogger(__name__)

SERVICE = "aird-features"

_SKIP_MESH = frozenset({"/health", "/healthz", "/ready", "/auth/verify"})


class MeshMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        if path in _SKIP_MESH or path.startswith("/static"):
            return await call_next(request)
        if not mesh_secret_ok(request.headers):
            return PlainTextResponse("mesh", status_code=401)
        return await call_next(request)


def _bootstrap_context() -> AppState:
    import aird.config as config
    import aird.constants as constants
    from aird.core.auth_secrets import resolve_cookie_secret
    from aird.main import _build_app_context, _init_database

    config.init_config()
    _init_database()
    app_context = _build_app_context()
    cookie_secret, _ = resolve_cookie_secret(constants.ROOT_DIR)
    from aird.core.bulk.engine import BulkEngine

    bulk_engine = BulkEngine(app_context, cookie_secret)
    settings = {
        "cookie_secret": cookie_secret,
        "ldap_server": config.LDAP_SERVER if getattr(config, "LDAP_ENABLED", False) else None,
        "db_conn": app_context.db_conn,
        "services": app_context.services,
        "app_context": app_context,
        "cloud_manager": app_context.cloud_manager,
        "room_manager": app_context.room_manager,
        "event_bus": app_context.event_bus,
        "bulk_engine": bulk_engine,
        "xsrf_cookies": True,
    }
    return AppState(
        settings=settings,
        template_path=templates_dir(),
        static_path=static_dir(),
        db_conn=app_context.db_conn,
        services=app_context.services,
    )


@asynccontextmanager
async def lifespan(app: Starlette):
    if not getattr(app.state, "aird", None):
        app.state.aird = _bootstrap_context()
    yield


def create_app(state: AppState | None = None) -> Starlette:
    import aird.config as config

    config.init_config()
    routes = [
        *health_routes,
        *auth_verify_routes,
        *auth_routes,
        *browse_routes,
        *file_ops_routes,
        *config_routes,
        *admin_routes,
        *share_routes,
        *extra_api_routes,
        *ws_routes,
        *ws_extra_routes,
        Mount("/static", StaticFiles(directory=static_dir()), name="static"),
    ]
    if config.LDAP_ENABLED:
        from aird.ms.routers.ldap_admin import routes as ldap_admin_routes

        routes.extend(ldap_admin_routes)
    application = Starlette(routes=routes, lifespan=lifespan)
    application.state.aird = state  # may be None until lifespan/bootstrap
    application.add_middleware(MeshMiddleware)
    if state is not None:
        application.state.aird = state
    else:
        # Eager bootstrap so cookie_secret exists before first request
        application.state.aird = _bootstrap_context()
    return application


def __getattr__(name: str):
    """Lazy ``app`` for Granian ``aird.ms.features:app``."""
    if name == "app":
        application = create_app()
        globals()["app"] = application
        return application
    raise AttributeError(name)


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    require_mesh_configuration(SERVICE)
    host = os.environ.get("AIRD_FEATURES_HOST", "127.0.0.1")
    port = int(os.environ.get("AIRD_FEATURES_PORT", "8001"))
    try:
        from granian import Granian
        from granian.constants import Interfaces
    except ImportError as exc:
        raise SystemExit(
            "granian is required for the features service: pip install granian"
        ) from exc

    # Force bootstrap before serve
    create_app()
    logger.info("aird-features (Granian ASGI) on %s:%s", host, port)
    Granian(
        "aird.ms.features:app",
        address=host,
        port=port,
        interface=Interfaces.ASGI,
        websockets=True,
    ).serve()


if __name__ == "__main__":
    main()
