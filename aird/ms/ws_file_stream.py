"""File tail/stream WebSocket — Starlette port of FileStreamHandler."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from collections import deque
from urllib.parse import unquote

import aiofiles
from starlette.websockets import WebSocket, WebSocketDisconnect

from aird.constants.input_limits import API_LAST_N_MAX
from aird.core.filter_expression import FilterExpression
from aird.handlers.api_handlers import FileStreamHandler
from aird.handlers.constants import AUTH_REQUIRED
from aird.core.security import is_within_root
from aird.ms.ctx import current_user
from aird.ms.ws_support import (
    StarletteWsBridge,
    register_managed_connection,
    reject_oversized_ws_message,
    request_from_websocket,
    require_ws_user,
    send_json,
    ws_origin_ok,
)

logger = logging.getLogger(__name__)

_MANAGER = FileStreamHandler.connection_manager


class _FileStreamAdapter:
    connection_manager = _MANAGER

    def __init__(self, bridge: StarletteWsBridge, request):
        self._bridge = bridge
        self.request = request
        self.file_path: str | None = None
        self.file = None
        self.is_streaming = False
        self.line_buffer: deque = deque(maxlen=1000)
        self.filter_expression: str | None = None
        self.stop_event = asyncio.Event()
        self._stream_task: asyncio.Task | None = None

    async def write_message(self, message: str | bytes) -> None:
        text = message.decode("utf-8") if isinstance(message, bytes) else message
        await self._bridge._send(text)

    def close(self, code: int = 1000, reason: str = "") -> None:
        self._bridge.close(code=code, reason=reason)

    def get_current_user(self):
        return current_user(self.request)

    _handle_stream_file_action = FileStreamHandler._handle_stream_file_action
    _send_line_with_filter = FileStreamHandler._send_line_with_filter
    stream_file = FileStreamHandler.stream_file


async def file_stream_websocket(websocket: WebSocket) -> None:
    request = request_from_websocket(websocket)
    if not ws_origin_ok(websocket, request):
        await websocket.close(code=1008, reason="Invalid origin")
        return

    await websocket.accept()
    bridge = StarletteWsBridge(websocket)

    user = await require_ws_user(
        websocket,
        request,
        close_reason=AUTH_REQUIRED,
    )
    if not user:
        return

    if not await register_managed_connection(websocket, bridge, _MANAGER):
        return

    adapter = _FileStreamAdapter(bridge, request)
    rel_path = websocket.path_params.get("path", "")
    from aird.core.browse_paths import resolve_for_user
    from aird.ms.ctx import user_root_for
    from aird.ms.deps import db_conn

    abspath, confine = resolve_for_user(
        str(user.get("username") or ""),
        unquote(rel_path),
        user_root_for(user),
        db_conn(request),
    )
    if (
        not abspath
        or not confine
        or not is_within_root(abspath, confine)
        or not os.path.isfile(abspath)
    ):
        await websocket.close(code=1003, reason="File not found")
        _MANAGER.remove_connection(bridge)
        return
    adapter.file_path = abspath

    n_str = websocket.query_params.get("n", "1000")
    try:
        n_lines = int(n_str)
        if n_lines <= 0:
            n_lines = 1000
    except ValueError:
        n_lines = 1000
    n_lines = min(n_lines, API_LAST_N_MAX)

    adapter.line_buffer = deque(maxlen=n_lines)
    adapter.filter_expression = websocket.query_params.get("filter")

    expr = None
    if adapter.filter_expression:
        try:
            expr = FilterExpression(adapter.filter_expression)
        except (ValueError, TypeError, re.error) as parse_err:
            logger.debug(
                "Invalid filter expression %s: %s",
                adapter.filter_expression,
                parse_err,
            )

    try:
        async with aiofiles.open(
            adapter.file_path, "r", encoding="utf-8", errors="replace"
        ) as f:
            async for line in f:
                if expr and not expr.matches(line):
                    continue
                adapter.line_buffer.append(line)
        for line in adapter.line_buffer:
            await send_json(websocket, {"type": "line", "data": line.strip()})
    except Exception as e:
        logger.exception("Error reading file: %s", adapter.file_path)
        await send_json(websocket, {"type": "error", "message": str(e)})
        _MANAGER.remove_connection(bridge)
        return

    adapter.is_streaming = True
    adapter._stream_task = asyncio.create_task(adapter.stream_file())

    try:
        while True:
            raw = await websocket.receive_text()
            if reject_oversized_ws_message(raw):
                await send_json(
                    websocket, {"type": "error", "message": "Message too large"}
                )
                continue

            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                await send_json(
                    websocket, {"type": "error", "message": "Invalid JSON payload"}
                )
                continue

            action = data.get("action")
            if not action:
                await send_json(
                    websocket,
                    {"type": "error", "message": "Invalid request: action is required"},
                )
                continue

            if action == "stop":
                adapter.is_streaming = False
                adapter.stop_event.set()
                continue
            if action == "start":
                adapter.is_streaming = True
                adapter._stream_task = asyncio.create_task(adapter.stream_file())
                continue
            if action == "lines":
                try:
                    new_size = int(data.get("lines", 0))
                    if new_size > 0:
                        adapter.line_buffer = deque(adapter.line_buffer, maxlen=new_size)
                except (ValueError, TypeError):
                    pass
                continue
            if action == "filter":
                adapter.filter_expression = data.get("filter")
                continue
            if action == "stream_file":
                await adapter._handle_stream_file_action(data)
                continue

            await send_json(websocket, {"type": "error", "message": "Unknown action"})
    except WebSocketDisconnect:
        pass
    finally:
        adapter.is_streaming = False
        adapter.stop_event.set()
        if adapter.file:
            try:
                await adapter.file.close()
            except Exception as close_err:
                logger.debug("Error closing file stream: %s", close_err)
        _MANAGER.remove_connection(bridge)
