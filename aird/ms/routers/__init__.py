"""Starlette route tables for the aird features ASGI app."""

from __future__ import annotations

from aird.ms.routers.admin import routes as admin_routes
from aird.ms.routers.auth import routes as auth_routes
from aird.ms.routers.auth_verify import routes as auth_verify_routes
from aird.ms.routers.browse import routes as browse_routes
from aird.ms.routers.config_api import routes as config_routes
from aird.ms.routers.extra_api import routes as extra_api_routes
from aird.ms.routers.file_ops import routes as file_ops_routes
from aird.ms.routers.health import routes as health_routes
from aird.ms.routers.shares import routes as share_routes
from aird.ms.routers.ws import routes as ws_routes
from aird.ms.routers.ws_extra import routes as ws_extra_routes

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
]

__all__ = ["routes"]
