"""WebSocket handler for background folder size scans."""

from __future__ import annotations

import asyncio
import json
import logging
import os

import tornado.websocket

from aird.core.folder_size import FOLDER_SIZE_BATCH_FILES, FolderSizeWalker
from aird.core.security import is_valid_websocket_origin, is_within_root
from aird.handlers.base_handler import (
    BaseHandler,
    ManagedWebSocketMixin,
    authenticate_handler,
    get_user_root,
)
from aird.handlers.constants import AUTH_REQUIRED
from aird.utils.util import WebSocketConnectionManager, format_size

logger = logging.getLogger(__name__)


class FolderSizeWebSocketHandler(
    ManagedWebSocketMixin, tornado.websocket.WebSocketHandler
):
    """Scan folder sizes asynchronously; stream progress to the browse UI."""

    connection_manager = WebSocketConnectionManager(
        "file_streaming", default_max_connections=200, default_idle_timeout=300
    )

    def __init__(self, application, request, **kwargs):
        super().__init__(application, request, **kwargs)
        self._cancelled = False
        self._scan_task: asyncio.Task | None = None

    def get_current_user(self):
        return authenticate_handler(self)

    async def _send_json(self, payload: dict) -> None:
        try:
            await self.write_message(json.dumps(payload))
        except (tornado.websocket.WebSocketClosedError, RuntimeError):
            pass

    async def open(self):
        if not self.get_current_user():
            self.close(code=1008, reason=AUTH_REQUIRED)
            return
        if not self.register_connection():
            return
        await self._send_json({"type": "ready"})

    async def on_message(self, message):
        if isinstance(message, bytes):
            return
        try:
            data = json.loads(message)
        except json.JSONDecodeError:
            await self._send_json({"type": "error", "message": "Invalid JSON"})
            return

        action = (data.get("action") or "").strip()
        if action == "cancel":
            self._cancelled = True
            if self._scan_task and not self._scan_task.done():
                self._scan_task.cancel()
            return

        if action == "scan":
            folders = data.get("folders")
            if not isinstance(folders, list) or not folders:
                await self._send_json(
                    {"type": "error", "message": "folders list is required"}
                )
                return
            if self._scan_task and not self._scan_task.done():
                await self._send_json(
                    {"type": "error", "message": "Scan already in progress"}
                )
                return
            self._cancelled = False
            self._scan_task = asyncio.create_task(self._scan_folders(folders))
            return

        await self._send_json({"type": "error", "message": "Unknown action"})

    def _resolve_folder_abspath(self, user_root: str, rel_path: str) -> str | None:
        rel = rel_path.replace("\\", "/").strip().strip("/")
        if not rel or ".." in rel.split("/"):
            return None
        abs_path = os.path.abspath(os.path.join(user_root, rel))
        if not is_within_root(abs_path, user_root) or not os.path.isdir(abs_path):
            return None
        return abs_path

    async def _scan_one_folder(self, user_root: str, rel_path: str) -> None:
        abs_path = self._resolve_folder_abspath(user_root, rel_path)
        if not abs_path:
            await self._send_json(
                {
                    "type": "folder_error",
                    "path": rel_path,
                    "message": "Folder not found or access denied",
                }
            )
            return

        walker = FolderSizeWalker(abs_path)
        last_emit = 0
        while not self._cancelled:
            total, count, done = await asyncio.to_thread(
                walker.step, FOLDER_SIZE_BATCH_FILES
            )
            emit = done or count - last_emit >= FOLDER_SIZE_BATCH_FILES
            if emit:
                last_emit = count
                await self._send_json(
                    {
                        "type": "folder_size" if done else "folder_progress",
                        "path": rel_path.replace("\\", "/").strip("/"),
                        "bytes": total,
                        "files": count,
                        "size_str": format_size(total),
                        "done": done,
                    }
                )
            if done:
                return
            await asyncio.sleep(0)

    async def _scan_folders(self, folders: list) -> None:
        user_root = get_user_root(self)
        try:
            seen: set[str] = set()
            for raw in folders:
                if self._cancelled:
                    break
                if not isinstance(raw, str):
                    continue
                rel = raw.replace("\\", "/").strip().strip("/")
                if not rel or rel in seen:
                    continue
                seen.add(rel)
                if _ws_check_access(self, "file.list", resource_path=rel):
                    await self._send_json(
                        {
                            "type": "folder_error",
                            "path": rel,
                            "message": "Access denied",
                        }
                    )
                    continue
                await self._scan_one_folder(user_root, rel)
            if not self._cancelled:
                await self._send_json({"type": "scan_complete"})
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Folder size scan failed")
            await self._send_json(
                {"type": "error", "message": "Folder size scan failed"}
            )

    def on_close(self):
        self._cancelled = True
        if self._scan_task and not self._scan_task.done():
            self._scan_task.cancel()
        super().on_close()

    def check_origin(self, origin):
        return is_valid_websocket_origin(self, origin)


class _WebSocketPEP(BaseHandler):
    def __init__(self, ws_handler):
        super().__init__(ws_handler.application, ws_handler.request)
        self._ws_handler = ws_handler

    def get_current_user(self):
        return self._ws_handler.get_current_user()


def _ws_check_access(handler, action: str, resource_path: str | None = None) -> bool:
    try:
        decision = _WebSocketPEP(handler).check_access(action, resource_path=resource_path)
        return decision is not None and decision.is_deny
    except Exception:
        logger.debug("WS ABAC check failed", exc_info=True)
        return False
