"""Shared Granian runtime for independently deployed Aird services."""

from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from typing import Iterable

from starlette.applications import Starlette
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import PlainTextResponse
from starlette.routing import BaseRoute, Mount
from starlette.staticfiles import StaticFiles

from aird.ms.common import mesh_secret_ok, require_mesh_configuration
from aird.ms.ctx import AppState
from aird.ms.render import static_dir, templates_dir

logger = logging.getLogger(__name__)

_HEALTH_PATHS = frozenset({"/health", "/healthz", "/ready"})


class MeshMiddleware(BaseHTTPMiddleware):
    """Reject direct backend traffic; Caddy is the only trusted ingress."""

    async def dispatch(self, request: Request, call_next):
        if request.url.path in _HEALTH_PATHS:
            return await call_next(request)
        if not mesh_secret_ok(request.headers):
            return PlainTextResponse("mesh", status_code=401)
        return await call_next(request)


def bootstrap_context() -> AppState:
    import aird.config as config
    import aird.constants as constants
    from aird.core.auth_secrets import resolve_cookie_secret
    from aird.core.bulk.engine import BulkEngine
    from aird.main import _build_app_context, _init_database

    config.init_config()
    _init_database()
    app_context = _build_app_context()
    cookie_secret, _ = resolve_cookie_secret(constants.ROOT_DIR)
    settings = {
        "cookie_secret": cookie_secret,
        "ldap_server": config.LDAP_SERVER if getattr(config, "LDAP_ENABLED", False) else None,
        "db_conn": app_context.db_conn,
        "services": app_context.services,
        "app_context": app_context,
        "cloud_manager": app_context.cloud_manager,
        "room_manager": app_context.room_manager,
        "event_bus": app_context.event_bus,
        "bulk_engine": BulkEngine(app_context, cookie_secret),
        "xsrf_cookies": True,
    }
    return AppState(
        settings=settings,
        template_path=templates_dir(),
        static_path=static_dir(),
        db_conn=app_context.db_conn,
        services=app_context.services,
    )


def route_path(route: BaseRoute) -> str:
    return str(getattr(route, "path", ""))


def select_routes(
    routes: Iterable[BaseRoute],
    *,
    exact: Iterable[str] = (),
    prefixes: Iterable[str] = (),
) -> list[BaseRoute]:
    exact_set = set(exact)
    prefix_tuple = tuple(prefixes)
    return [
        route
        for route in routes
        if route_path(route) in exact_set
        or (prefix_tuple and route_path(route).startswith(prefix_tuple))
    ]


def exclude_routes(
    routes: Iterable[BaseRoute],
    *,
    exact: Iterable[str] = (),
    prefixes: Iterable[str] = (),
) -> list[BaseRoute]:
    excluded = set(exact)
    prefix_tuple = tuple(prefixes)
    return [
        route
        for route in routes
        if route_path(route) not in excluded
        and not (prefix_tuple and route_path(route).startswith(prefix_tuple))
    ]


def create_service_app(
    service: str,
    routes: Iterable[BaseRoute],
    *,
    state: AppState | None = None,
    mount_static: bool = False,
) -> Starlette:
    @asynccontextmanager
    async def lifespan(app: Starlette):
        if not getattr(app.state, "aird", None):
            app.state.aird = bootstrap_context()
        yield

    service_routes = list(routes)
    if mount_static:
        service_routes.append(
            Mount("/static", StaticFiles(directory=static_dir()), name="static")
        )
    application = Starlette(routes=service_routes, lifespan=lifespan)
    application.state.aird_service = service
    application.state.aird = state
    application.add_middleware(MeshMiddleware)
    return application


def serve(module: str, service: str, host_env: str, port_env: str, default_port: int) -> None:
    logging.basicConfig(level=logging.INFO)
    require_mesh_configuration(service)
    host = os.environ.get(host_env, "127.0.0.1")
    port = int(os.environ.get(port_env, str(default_port)))
    try:
        from granian import Granian
        from granian.constants import Interfaces
    except ImportError as exc:
        raise SystemExit("granian is required; run: uv sync") from exc

    logger.info("%s (Granian ASGI) on %s:%s", service, host, port)
    Granian(
        f"{module}:app",
        address=host,
        port=port,
        interface=Interfaces.ASGI,
        websockets=True,
    ).serve()
