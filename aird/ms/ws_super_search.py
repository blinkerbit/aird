"""Super Search WebSocket session — Starlette port of SuperSearchWebSocketHandler."""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any
from urllib.parse import quote

from starlette.requests import Request
from starlette.websockets import WebSocket, WebSocketDisconnect

from aird.core.input_validation import validate_super_search_glob, validate_ws_search
from aird.constants.input_limits import InputTooLongError
from aird.handlers.api_handlers import SuperSearchWebSocketHandler
from aird.handlers.constants import AUTH_EXPIRED, AUTH_REQUIRED, REDIRECT_SEARCH
from aird.ms.ctx import current_user
from aird.ms.ws_support import (
    StarletteWsBridge,
    register_managed_connection,
    reject_oversized_ws_message,
    request_from_websocket,
    require_ws_user,
    send_auth_required_and_close,
    send_json,
    ws_origin_ok,
)

logger = logging.getLogger(__name__)

_HANDLER = SuperSearchWebSocketHandler
_MANAGER = _HANDLER.connection_manager


class _SuperSearchAdapter:
    """Minimal handler surface so Tornado search methods run unchanged."""

    connection_manager = _MANAGER
    _walk_scan_interval = _HANDLER._walk_scan_interval

    def __init__(self, bridge: StarletteWsBridge, request: Request):
        self._bridge = bridge
        self.request = request
        self.stop_event = asyncio.Event()
        self.search_task: asyncio.Task | None = None

    def write_message(self, message: str | bytes) -> None:
        self._bridge.write_message(message)

    def close(self, code: int = 1000, reason: str = "") -> None:
        self._bridge.close(code=code, reason=reason)

    def get_current_user(self) -> Any:
        return current_user(self.request)

    # Bind Tornado handler methods
    _send_auth_required_and_close = _HANDLER._send_auth_required_and_close
    _await_cancellation_and_start_new = _HANDLER._await_cancellation_and_start_new
    _file_matches_pattern = staticmethod(_HANDLER._file_matches_pattern)
    _emit_scanning = _HANDLER._emit_scanning
    _emit_walk_tick = _HANDLER._emit_walk_tick
    _search_file_content = _HANDLER._search_file_content
    _match_filename_search = _HANDLER._match_filename_search
    _search_one_file = _HANDLER._search_one_file
    _process_walk_file = _HANDLER._process_walk_file
    _run_search_walk = _HANDLER._run_search_walk
    _yield_and_check_auth = _HANDLER._yield_and_check_auth
    _send_search_completion = _HANDLER._send_search_completion
    perform_search = _HANDLER.perform_search
    send_match = _HANDLER.send_match


async def super_search_websocket(websocket: WebSocket) -> None:
    request = request_from_websocket(websocket)
    if not ws_origin_ok(websocket, request):
        await websocket.close(code=1008, reason="Invalid origin")
        return

    await websocket.accept()
    bridge = StarletteWsBridge(websocket)

    user = await require_ws_user(
        websocket,
        request,
        auth_message="Authentication required. Please log in.",
        redirect="/login?next=" + quote(request.url.path),
        close_reason=AUTH_REQUIRED,
    )
    if not user:
        return

    logger.info(
        "SuperSearchWebSocket: User authenticated - %s",
        user.get("username", "unknown") if isinstance(user, dict) else user,
    )

    if not await register_managed_connection(websocket, bridge, _MANAGER):
        return

    adapter = _SuperSearchAdapter(bridge, request)

    try:
        while True:
            raw = await websocket.receive_text()
            if reject_oversized_ws_message(raw):
                await send_json(
                    websocket, {"type": "error", "message": "Message too large"}
                )
                continue

            if not current_user(request):
                logger.warning(
                    "SuperSearchWebSocket: Authentication failed on message - session expired"
                )
                adapter._send_auth_required_and_close(
                    AUTH_EXPIRED, REDIRECT_SEARCH, AUTH_EXPIRED
                )
                break

            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                await send_json(
                    websocket, {"type": "error", "message": "Invalid JSON format"}
                )
                continue

            pattern = data.get("pattern")
            search_text = data.get("search_text")
            if not pattern or not search_text:
                await send_json(
                    websocket,
                    {
                        "type": "error",
                        "message": "Both pattern and search_text are required",
                    },
                )
                continue

            try:
                pattern, search_text = validate_ws_search(pattern, search_text)
            except InputTooLongError:
                await send_json(
                    websocket,
                    {"type": "error", "message": "Search parameters too long"},
                )
                continue

            glob_err = validate_super_search_glob(pattern)
            if glob_err:
                await send_json(websocket, {"type": "error", "message": glob_err})
                continue

            if not current_user(request):
                adapter._send_auth_required_and_close(
                    AUTH_EXPIRED, REDIRECT_SEARCH, AUTH_EXPIRED
                )
                break

            search_mode = data.get("search_mode", "content")

            if adapter.search_task and not adapter.search_task.done():
                adapter.stop_event.set()
                adapter.search_task = asyncio.create_task(
                    adapter._await_cancellation_and_start_new(
                        (pattern, search_text, search_mode)
                    )
                )
            else:
                adapter.stop_event.clear()
                adapter.search_task = asyncio.create_task(
                    adapter.perform_search(pattern, search_text, search_mode)
                )
    except WebSocketDisconnect:
        pass
    finally:
        if adapter.search_task:
            adapter.stop_event.set()
        _MANAGER.remove_connection(bridge)
