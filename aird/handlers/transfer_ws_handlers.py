"""Legacy WebSocket file transfer (optional fallback; HTTP is primary)."""

from __future__ import annotations

import asyncio
import json
import logging
import mimetypes
import os
import tempfile
from collections import deque
from urllib.parse import unquote

import aiofiles
import tornado.websocket

import aird.constants as constants_module
from aird.constants.file_ops import (
    ACCESS_DENIED,
    FILE_TOO_LARGE_TEMPLATE,
    FILE_UPLOAD_DISABLED,
    UPLOAD_SAVE_FAILED,
)
from aird.constants import WS_TRANSFER_FRAME_BYTES
from aird.core.mmap_handler import MMapFileHandler
from aird.core.security import is_valid_websocket_origin, is_within_root
from aird.handlers.base_handler import (
    BaseHandler,
    ManagedWebSocketMixin,
    _display_username_from_dict,
    authenticate_handler,
    get_user_root,
)
from aird.handlers.constants import AUTH_REQUIRED
from aird.handlers.file_op_handlers import finalize_upload_to_disk
from aird.utils.util import WebSocketConnectionManager, is_feature_enabled

logger = logging.getLogger(__name__)

_TOKEN_ONLY_USERNAMES = {"token_user", "admin_token"}


def _ws_has_modify_privileges(handler) -> bool:
    user = handler.get_current_user()
    if not isinstance(user, dict):
        return False
    username = user.get("username")
    if not isinstance(username, str) or not username.strip():
        return False
    if username in _TOKEN_ONLY_USERNAMES:
        return False
    return str(user.get("role", "user")).lower() in {"admin", "user"}


def _ws_display_username(handler) -> str:
    user = handler.get_current_user()
    if not user:
        return "Guest"
    if isinstance(user, dict):
        return _display_username_from_dict(user)
    return str(user)


def _ws_db_conn(handler):
    app_ctx = handler.settings.get("app_context")
    if app_ctx is not None:
        return app_ctx.db_conn
    return handler.settings.get("db_conn")


def _ws_get_service(handler, name: str, default=None):
    app_ctx = handler.settings.get("app_context")
    if app_ctx is not None:
        return app_ctx.get_service(name, default)
    return handler.settings.get("services", {}).get(name, default)


class _WebSocketPEP(BaseHandler):
    """Reuse BaseHandler.check_access from WebSocket handlers."""

    def __init__(self, ws_handler):
        super().__init__(ws_handler.application, ws_handler.request)
        self._ws_handler = ws_handler

    def get_current_user(self):
        return self._ws_handler.get_current_user()


def _ws_check_access(handler, action: str, resource_path: str | None = None) -> bool:
    """Return True if access is denied."""
    try:
        decision = _WebSocketPEP(handler).check_access(action, resource_path=resource_path)
        return decision is not None and decision.is_deny
    except Exception:
        logger.debug("WS ABAC check failed", exc_info=True)
        return False


class FileTransferWebSocketHandler(
    ManagedWebSocketMixin, tornado.websocket.WebSocketHandler
):
    """Stream files over a single WebSocket (no HTTP-style chunk sessions)."""

    connection_manager = WebSocketConnectionManager(
        "file_streaming", default_max_connections=200, default_idle_timeout=300
    )

    def __init__(self, application, request, **kwargs):
        super().__init__(application, request, **kwargs)
        self._upload: dict | None = None
        self._upload_buffer: deque[bytes] = deque()
        self._upload_buffer_event = asyncio.Event()
        self._upload_writer_done = False
        self._upload_writer_task: asyncio.Task | None = None
        self._abort_upload_task: asyncio.Task | None = None
        self._cancelled = False
        self._download_task: asyncio.Task | None = None

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
            await self._handle_upload_binary(message)
            return
        if self.reject_oversized_ws_message(message):
            return
        try:
            data = json.loads(message)
        except json.JSONDecodeError:
            await self._send_json({"type": "error", "message": "Invalid JSON"})
            return

        action = (data.get("action") or "").strip()
        if action == "upload_start":
            await self._handle_upload_start(data)
        elif action == "upload_end":
            await self._handle_upload_end()
        elif action == "download":
            await self._handle_download(data)
        elif action == "cancel":
            self._cancelled = True
            await self._abort_upload()
            if self._download_task and not self._download_task.done():
                self._download_task.cancel()
        else:
            await self._send_json({"type": "error", "message": "Unknown action"})

    async def _handle_upload_start(self, data: dict) -> None:
        if self._upload is not None:
            await self._send_json({"type": "error", "message": "Upload already in progress"})
            return
        if not is_feature_enabled("file_upload", True):
            await self._send_json({"type": "error", "message": FILE_UPLOAD_DISABLED})
            return
        if not _ws_has_modify_privileges(self):
            await self._send_json({"type": "error", "message": ACCESS_DENIED})
            return

        upload_dir = unquote((data.get("upload_dir") or "").strip())
        filename = unquote((data.get("filename") or "").strip())
        total_size = data.get("total_size")

        if not filename or not isinstance(total_size, int) or total_size < 0:
            await self._send_json({"type": "error", "message": "Invalid upload metadata"})
            return

        if total_size > constants_module.MAX_FILE_SIZE:
            limit_mb = constants_module.UPLOAD_CONFIG.get("max_file_size_mb", 512)
            await self._send_json(
                {
                    "type": "error",
                    "message": FILE_TOO_LARGE_TEMPLATE.format(limit_mb=limit_mb),
                }
            )
            return

        if _ws_check_access(self, "file.write", resource_path=upload_dir or filename):
            await self._send_json({"type": "error", "message": ACCESS_DENIED})
            return

        fd, temp_path = tempfile.mkstemp(prefix="aird_ws_upload_")
        os.close(fd)
        aiofile = await aiofiles.open(temp_path, "wb")
        self._upload = {
            "upload_dir": upload_dir,
            "filename": filename,
            "total_size": total_size,
            "bytes_received": 0,
            "temp_path": temp_path,
            "aiofile": aiofile,
        }
        self._upload_writer_done = False
        self._upload_buffer_event.clear()
        self._upload_writer_task = asyncio.create_task(self._upload_writer_loop())
        await self._send_json({"type": "upload_started", "total_size": total_size})

    async def _handle_upload_binary(self, data: bytes) -> None:
        if not self._upload or self._cancelled:
            await self._send_json({"type": "error", "message": "No upload in progress"})
            return

        self._upload["bytes_received"] += len(data)
        if self._upload["bytes_received"] > self._upload["total_size"]:
            await self._abort_upload("Upload exceeds declared size")
            return

        self._upload_buffer.append(data)
        self._upload_buffer_event.set()

    async def _upload_writer_loop(self) -> None:
        """Drain the upload buffer continuously until signalled to stop."""
        try:
            while True:
                await self._upload_buffer_event.wait()
                self._upload_buffer_event.clear()
                while self._upload_buffer and self._upload:
                    chunk = self._upload_buffer.popleft()
                    await self._upload["aiofile"].write(chunk)
                if self._upload:
                    await self._upload["aiofile"].flush()
                if self._upload_writer_done:
                    return
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.debug("WS upload writer loop failed", exc_info=True)

    async def _finalize_upload_writer(self) -> None:
        self._upload_writer_done = True
        self._upload_buffer_event.set()
        if self._upload_writer_task is not None:
            try:
                await self._upload_writer_task
            except Exception:
                logger.debug("WS upload writer await failed", exc_info=True)
            self._upload_writer_task = None
        if self._upload and self._upload.get("aiofile"):
            try:
                await self._upload["aiofile"].close()
            except Exception:
                logger.debug("WS upload file close failed", exc_info=True)
            self._upload["aiofile"] = None

    async def _abort_upload(self, message: str | None = None) -> None:
        upload = self._upload
        self._upload = None
        self._upload_buffer.clear()
        await self._finalize_upload_writer()
        if upload:
            path = upload.get("temp_path")
            if path and os.path.isfile(path):
                try:
                    os.remove(path)
                except OSError:
                    logging.debug("WS upload temp remove failed", exc_info=True)
        if message:
            await self._send_json({"type": "error", "message": message})

    async def _handle_upload_end(self) -> None:
        if not self._upload:
            await self._send_json({"type": "error", "message": "No upload in progress"})
            return

        await self._finalize_upload_writer()
        upload = self._upload
        self._upload = None

        received = upload["bytes_received"]
        expected = upload["total_size"]
        if received != expected:
            try:
                os.remove(upload["temp_path"])
            except OSError:
                pass
            await self._send_json(
                {
                    "type": "error",
                    "message": f"Size mismatch: received {received}, expected {expected}",
                }
            )
            return

        user_root = get_user_root(self)
        success, status, message = await asyncio.to_thread(
            finalize_upload_to_disk,
            upload_dir=upload["upload_dir"],
            filename=upload["filename"],
            temp_path=upload["temp_path"],
            user_root=user_root,
            username=_ws_display_username(self),
            db_conn=_ws_db_conn(self),
            quota_service=_ws_get_service(self, "quota_service"),
            audit_service=_ws_get_service(self, "audit_service"),
            remote_ip=getattr(self.request, "remote_ip", None),
            upload_bytes=expected,
        )
        if success:
            await self._send_json({"type": "upload_complete", "message": message})
        else:
            await self._send_json({"type": "error", "message": message, "status": status})

    async def _handle_download(self, data: dict) -> None:
        if not is_feature_enabled("file_download", True):
            await self._send_json({"type": "error", "message": "File download is disabled"})
            return

        rel_path = (data.get("path") or "").strip().lstrip("/")
        if not rel_path:
            await self._send_json({"type": "error", "message": "path is required"})
            return

        if _ws_check_access(self, "file.read", resource_path=rel_path):
            await self._send_json({"type": "error", "message": ACCESS_DENIED})
            return

        user_root = get_user_root(self)
        abs_path = os.path.abspath(os.path.join(user_root, rel_path))
        if not is_within_root(abs_path, user_root) or not os.path.isfile(abs_path):
            await self._send_json({"type": "error", "message": "File not found"})
            return

        if self._download_task and not self._download_task.done():
            await self._send_json(
                {"type": "error", "message": "Download already in progress"}
            )
            return

        self._download_task = asyncio.create_task(self._stream_download(abs_path, rel_path))

    async def _stream_download(self, abs_path: str, rel_path: str) -> None:
        try:
            content_type = (
                mimetypes.guess_type(abs_path)[0] or "application/octet-stream"
            )
            file_size = os.path.getsize(abs_path)
            filename = os.path.basename(abs_path)
            await self._send_json(
                {
                    "type": "download_start",
                    "path": rel_path,
                    "filename": filename,
                    "content_type": content_type,
                    "size": file_size,
                    "chunk_size": WS_TRANSFER_FRAME_BYTES,
                }
            )
            async for chunk in MMapFileHandler.serve_file_chunk(
                abs_path, chunk_size=WS_TRANSFER_FRAME_BYTES
            ):
                if self._cancelled:
                    return
                await self.write_message(chunk, binary=True)
                await asyncio.sleep(0)
            await self._send_json({"type": "download_end"})
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.exception("WS download failed for %s", rel_path)
            await self._send_json({"type": "error", "message": str(e)})

    def on_close(self):
        self._cancelled = True
        if self._download_task and not self._download_task.done():
            self._download_task.cancel()
        if self._upload:
            self._abort_upload_task = asyncio.create_task(self._abort_upload())
        super().on_close()

    def check_origin(self, origin):
        return is_valid_websocket_origin(self, origin)
