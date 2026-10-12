"""Shared ASGI handler dependencies."""

from __future__ import annotations

from starlette.requests import Request

from aird.handlers.base_handler import DISPLAY_ADMIN_TOKEN
from aird.ms.ctx import current_user, get_state


def db_conn(request: Request):
    state = get_state(request)
    return state.db_conn or state.settings.get("db_conn")


def get_service(request: Request, name: str):
    state = get_state(request)
    services = state.services or state.settings.get("services") or {}
    return services.get(name)


def username_from_user(user) -> str | None:
    if not user:
        return None
    if isinstance(user, dict):
        name = user.get("username")
        if isinstance(name, str) and name.strip():
            return name.strip()
        return None
    text = str(user).strip()
    return text or None


def require_user(request: Request):
    user = current_user(request)
    if not user:
        raise PermissionError("Authentication required")
    return user


def is_admin_user(user) -> bool:
    if not isinstance(user, dict):
        return False
    return user.get("role") == "admin" or user.get("username") in (
        "admin_token",
        DISPLAY_ADMIN_TOKEN,
    )
