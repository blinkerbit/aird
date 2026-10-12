"""Caddy forward_auth endpoint: validate session cookies, return identity headers."""

from __future__ import annotations

import os

from starlette.requests import Request
from starlette.responses import Response
from starlette.routing import Route

from aird.handlers.base_handler import authenticate_handler
from aird.ms.common import HEADER_ROLE, HEADER_USER
from aird.ms.ctx import _AuthAdapter, get_state
from aird.ms.jwt_auth import bearer_identity


async def auth_verify(request: Request):
    state = get_state(request)
    user = bearer_identity(request) or authenticate_handler(
        _AuthAdapter(request, state.settings)
    )

    if user and isinstance(user, dict) and user.get("username"):
        username = str(user["username"])
        role = str(user.get("role") or "user")
        return Response(
            status_code=200,
            headers={
                HEADER_USER: username,
                HEADER_ROLE: role,
            },
        )

    if os.environ.get("AIRD_DEV_OPEN_AUTH", "").strip() == "1":
        return Response(
            status_code=200,
            headers={
                HEADER_USER: "dev",
                HEADER_ROLE: "user",
            },
        )

    return Response("unauthenticated", status_code=401)


routes = [
    Route("/auth/verify", auth_verify, methods=["GET", "HEAD"]),
]
