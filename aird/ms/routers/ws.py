"""Starlette WebSocket routes — ports of Tornado WS handlers in api_handlers."""

from __future__ import annotations

import json
import logging

from starlette.routing import WebSocketRoute
from starlette.websockets import WebSocket

import aird.constants as constants_module
from aird.handlers.api_handlers import (
    FeatureFlagSocketHandler,
    RuntimeConfigSocketHandler,
)
from aird.handlers.constants import AUTH_REQUIRED
from aird.ms.ws_file_stream import file_stream_websocket
from aird.ms.ws_super_search import super_search_websocket
from aird.ms.ws_support import (
    StarletteWsBridge,
    keepalive_receive_loop,
    register_managed_connection,
    request_from_websocket,
    require_ws_user,
    ws_origin_ok,
)
from aird.utils.util import get_current_feature_flags

logger = logging.getLogger(__name__)

_FEATURE_MANAGER = FeatureFlagSocketHandler.connection_manager
_RUNTIME_MANAGER = RuntimeConfigSocketHandler.connection_manager


async def feature_flags_websocket(websocket: WebSocket) -> None:
    request = request_from_websocket(websocket)
    if not ws_origin_ok(websocket, request):
        await websocket.close(code=1008, reason="Invalid origin")
        return

    await websocket.accept()
    bridge = StarletteWsBridge(websocket)

    user = await require_ws_user(websocket, request, close_reason=AUTH_REQUIRED)
    if not user:
        return

    if not await register_managed_connection(websocket, bridge, _FEATURE_MANAGER):
        return

    await websocket.send_text(json.dumps(get_current_feature_flags()))

    async def _on_message(_text: str) -> None:
        await websocket.send_text(json.dumps(get_current_feature_flags()))

    try:
        await keepalive_receive_loop(websocket, on_text=_on_message)
    finally:
        _FEATURE_MANAGER.remove_connection(bridge)


async def runtime_config_websocket(websocket: WebSocket) -> None:
    request = request_from_websocket(websocket)
    if not ws_origin_ok(websocket, request):
        await websocket.close(code=1008, reason="Invalid origin")
        return

    await websocket.accept()
    bridge = StarletteWsBridge(websocket)

    user = await require_ws_user(websocket, request, close_reason=AUTH_REQUIRED)
    if not user:
        return

    if not await register_managed_connection(websocket, bridge, _RUNTIME_MANAGER):
        return

    payload = constants_module.get_effective_transfer_strategy()
    await websocket.send_text(json.dumps(payload))

    async def _on_message(_text: str) -> None:
        await websocket.send_text(
            json.dumps(constants_module.get_effective_transfer_strategy())
        )

    try:
        await keepalive_receive_loop(websocket, on_text=_on_message)
    finally:
        _RUNTIME_MANAGER.remove_connection(bridge)


routes = [
    WebSocketRoute("/search/ws", super_search_websocket),
    WebSocketRoute("/stream/{path:path}", file_stream_websocket),
    WebSocketRoute("/features", feature_flags_websocket),
    WebSocketRoute("/runtime-config", runtime_config_websocket),
]
