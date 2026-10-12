"""Runtime config and feature-flag API routes."""

from __future__ import annotations

from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

import aird.constants as constants_module
from aird.ms.routers._helpers import require_user
from aird.utils.util import get_current_feature_flags


async def api_features(request: Request) -> Response:
    user = require_user(request)
    if isinstance(user, Response):
        return user
    return JSONResponse(get_current_feature_flags())


async def api_runtime_config(request: Request) -> Response:
    user = require_user(request)
    if isinstance(user, Response):
        return user

    return JSONResponse(
        constants_module.get_effective_transfer_strategy(),
        headers={"Cache-Control": "no-store"},
    )


routes = [
    Route("/api/features", api_features, methods=["GET"]),
    Route("/api/runtime-config", api_runtime_config, methods=["GET"]),
]
