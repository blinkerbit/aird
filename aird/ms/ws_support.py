"""Shared helpers for Starlette WebSocket endpoints (Tornado protocol compatibility)."""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from starlette.requests import Request
from starlette.websockets import WebSocket, WebSocketDisconnect

import aird.constants as constants_module
from aird.core.security import is_valid_websocket_origin
from aird.ms.ctx import current_user, get_state
from aird.utils.util import WebSocketConnectionManager

logger = logging.getLogger(__name__)


class WebSocketRequestShim:
    """HTTP-shaped Request over a WebSocket scope for auth/origin helpers.

    Starlette ``Request`` asserts ``scope["type"] == "http"``, so we cannot wrap
    a live WebSocket scope directly. This copies the scope as HTTP and exposes
    Tornado-style ``host`` / ``protocol`` for ``is_valid_websocket_origin``.
    """

    def __init__(self, websocket: WebSocket):
        scope = dict(websocket.scope)
        scope["type"] = "http"
        scope.setdefault("method", "GET")
        # Do not bind websocket.receive — that would steal WS frames.
        async def _empty_receive() -> dict[str, Any]:
            return {"type": "http.disconnect"}

        self._inner = Request(scope, _empty_receive)

    @property
    def host(self) -> str:
        host = self._inner.headers.get("host")
        if host:
            return host
        return self._inner.url.netloc or "localhost"

    @property
    def protocol(self) -> str:
        forwarded = self._inner.headers.get("x-forwarded-proto")
        if forwarded:
            return forwarded.split(",", 1)[0].strip()
        scheme = self._inner.url.scheme or "http"
        if scheme == "ws":
            return "http"
        if scheme == "wss":
            return "https"
        return scheme

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)


class _OriginAdapter:
    """Minimal handler surface for ``is_valid_websocket_origin``."""

    def __init__(self, request: Any, settings: dict[str, Any]):
        self.request = request
        self.settings = settings


def ws_origin_ok(websocket: WebSocket, request: Any) -> bool:
    origin = websocket.headers.get("origin", "")
    settings = get_state(request).settings
    return is_valid_websocket_origin(_OriginAdapter(request, settings), origin)


class StarletteWsBridge:
    """Tornado-compatible ``write_message`` / ``close`` for connection managers."""

    def __init__(self, websocket: WebSocket):
        self.websocket = websocket
        self._closed = False

    def write_message(self, message: str | bytes) -> None:
        if self._closed:
            return
        text = message.decode("utf-8") if isinstance(message, bytes) else message
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(self._send(text))
        except RuntimeError:
            logger.debug("No running loop for WS send")

    async def _send(self, text: str) -> None:
        try:
            await self.websocket.send_text(text)
        except Exception:
            self._closed = True

    def close(self, code: int = 1000, reason: str = "") -> None:
        if self._closed:
            return
        self._closed = True
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(self.websocket.close(code=code, reason=reason))
        except RuntimeError:
            pass

    def ping(self) -> None:
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(self.websocket.send_text('{"type":"ping"}'))
        except RuntimeError:
            pass


def reject_oversized_ws_message(message: str | bytes) -> bool:
    limit = constants_module.WS_JSON_MESSAGE_MAX_BYTES
    if isinstance(message, bytes):
        nbytes = len(message)
    else:
        nbytes = len(str(message).encode("utf-8", errors="replace"))
    return nbytes > limit


async def send_json(websocket: WebSocket, payload: dict[str, Any]) -> None:
    await websocket.send_text(json.dumps(payload))


async def send_auth_required_and_close(
    websocket: WebSocket,
    *,
    message: str,
    redirect: str,
    close_reason: str,
) -> None:
    try:
        await send_json(
            websocket,
            {"type": "auth_required", "message": message, "redirect": redirect},
        )
    except Exception:
        pass
    try:
        await websocket.close(code=1008, reason=close_reason)
    except Exception:
        pass


async def register_managed_connection(
    websocket: WebSocket,
    bridge: StarletteWsBridge,
    manager: WebSocketConnectionManager,
) -> bool:
    if manager.add_connection(bridge):
        return True
    try:
        await send_json(
            websocket,
            {
                "type": "error",
                "message": "Connection limit exceeded. Please try again later.",
            },
        )
    except Exception:
        pass
    try:
        await websocket.close(code=1013, reason="Connection limit exceeded")
    except Exception:
        pass
    return False


async def require_ws_user(
    websocket: WebSocket,
    request: Any,
    *,
    auth_message: str = "Authentication required. Please log in.",
    redirect: str = "/login",
    close_reason: str = "Authentication required",
) -> dict[str, Any] | None:
    user = current_user(request)
    if user:
        return user
    await send_auth_required_and_close(
        websocket,
        message=auth_message,
        redirect=redirect,
        close_reason=close_reason,
    )
    return None


async def keepalive_receive_loop(
    websocket: WebSocket,
    on_text: Any | None = None,
) -> None:
    """Read until disconnect; optionally dispatch inbound text."""
    try:
        while True:
            message = await websocket.receive()
            if message["type"] == "websocket.disconnect":
                break
            if message["type"] != "websocket.receive":
                continue
            data = message.get("text")
            if data is None and message.get("bytes") is not None:
                data = message["bytes"].decode("utf-8", errors="replace")
            if on_text and data is not None:
                await on_text(data)
    except WebSocketDisconnect:
        pass


def request_from_websocket(websocket: WebSocket) -> WebSocketRequestShim:
    return WebSocketRequestShim(websocket)
