"""Native socketify WebSocket file upload (/ws/file-transfer)."""

from __future__ import annotations

import json
import logging
import os
import tempfile
from typing import Any
from urllib.parse import unquote

from socketify import OpCode

import aird.constants as constants
from aird.core.transfer_native import write_fd
from aird.handlers.base_handler import get_user_root
from aird.handlers.file_op_handlers import (
    _remove_staged_upload_temp,
    finalize_upload_to_disk,
)

logger = logging.getLogger(__name__)

_FRAME = max(int(getattr(constants, "WS_TRANSFER_FRAME_BYTES", 0) or 0), 8 * 1024 * 1024)


def _send_json(ws, payload: dict) -> None:
    try:
        ws.send(json.dumps(payload), OpCode.TEXT)
    except Exception:
        logger.debug("ws json send failed", exc_info=True)


def _get_state(ws) -> dict:
    data = ws.get_user_data()
    return data if isinstance(data, dict) else {}


def _set_state(ws, **kwargs) -> dict:
    """Mutate upgrade user-data dict in place (socketify has no set_user_data)."""
    data = _get_state(ws)
    data.update(kwargs)
    return data


def register_file_transfer_ws(app, tornado_app, *, auth_user_fn) -> None:
    """Binary-frame uploads over /ws/file-transfer."""

    def upgrade(res, req, socket_context):
        try:
            req.preserve()
        except Exception:
            pass
        user = auth_user_fn(tornado_app, req)
        res.upgrade(
            req.get_header("sec-websocket-key"),
            req.get_header("sec-websocket-protocol"),
            req.get_header("sec-websocket-extensions"),
            socket_context,
            {"user": user, "upload": None},
        )

    def open_handler(ws):
        state = _get_state(ws)
        if not state.get("user"):
            ws.close()
            return
        _send_json(ws, {"type": "ready"})

    def message_handler(ws, message, opcode):
        if opcode == OpCode.BINARY or isinstance(message, (bytes, bytearray, memoryview)):
            _on_binary(ws, tornado_app, bytes(message))
            return
        try:
            text = message.decode("utf-8") if isinstance(message, (bytes, bytearray)) else str(message)
            data = json.loads(text)
        except Exception:
            _send_json(ws, {"type": "error", "message": "Invalid JSON"})
            return
        action = (data.get("action") or "").strip()
        if action == "upload_start":
            _on_upload_start(ws, tornado_app, data)
        elif action == "upload_end":
            _on_upload_end(ws, tornado_app)
        elif action == "cancel":
            _abort(ws, None)
        else:
            _send_json(ws, {"type": "error", "message": "Unknown action"})

    def close_handler(ws, code, message):
        _abort(ws, None)

    app.ws(
        "/ws/file-transfer",
        {
            "compression": 0,
            "max_payload_length": _FRAME + (4 * 1024 * 1024),
            "idle_timeout": 120,
            "upgrade": upgrade,
            "open": open_handler,
            "message": message_handler,
            "close": close_handler,
        },
    )


def _mini_handler(tornado_app, user) -> Any:
    class _H:
        application = tornado_app

        def get_current_user(self):
            return user

    return _H()


def _on_upload_start(ws, tornado_app, data: dict) -> None:
    state = _get_state(ws)
    if state.get("upload"):
        _send_json(ws, {"type": "error", "message": "Upload already in progress"})
        return
    user = state.get("user")
    if not user:
        _send_json(ws, {"type": "error", "message": "Authentication required"})
        return

    upload_dir = unquote((data.get("upload_dir") or "").strip())
    filename = unquote((data.get("filename") or "").strip())
    total_size = data.get("total_size")
    if not filename or not isinstance(total_size, int) or total_size < 0:
        _send_json(ws, {"type": "error", "message": "Invalid upload metadata"})
        return
    if total_size > constants.MAX_FILE_SIZE:
        _send_json(ws, {"type": "error", "message": "File too large"})
        return

    handler = _mini_handler(tornado_app, user)
    user_root = get_user_root(handler)
    os.makedirs(user_root, exist_ok=True)
    fd, temp_path = tempfile.mkstemp(prefix=".aird_ws_up_", dir=user_root)
    _set_state(
        ws,
        upload={
            "upload_dir": upload_dir,
            "filename": filename,
            "total_size": total_size,
            "bytes_received": 0,
            "temp_path": temp_path,
            "fd": fd,
            "user_root": user_root,
        },
    )
    _send_json(ws, {"type": "upload_started", "total_size": total_size})


def _on_binary(ws, tornado_app, data: bytes) -> None:
    state = _get_state(ws)
    upload = state.get("upload")
    if not upload:
        _send_json(ws, {"type": "error", "message": "No upload in progress"})
        return
    try:
        write_fd(int(upload["fd"]), data)
    except OSError:
        logger.exception("WS upload write failed")
        _abort(ws, "Upload save failed")
        return
    upload["bytes_received"] = int(upload["bytes_received"]) + len(data)
    if upload["bytes_received"] > upload["total_size"]:
        _abort(ws, "Upload exceeds declared size")
        return
    _set_state(ws, upload=upload)
    # Ack so the browser UI tracks bytes on disk, not the WS send buffer.
    _send_json(
        ws,
        {
            "type": "upload_progress",
            "received": upload["bytes_received"],
            "total": upload["total_size"],
        },
    )


def _on_upload_end(ws, tornado_app) -> None:
    state = _get_state(ws)
    upload = state.get("upload")
    if not upload:
        _send_json(ws, {"type": "error", "message": "No upload in progress"})
        return

    fd = upload.get("fd")
    if fd is not None and int(fd) >= 0:
        try:
            os.close(int(fd))
        except OSError:
            pass
        upload["fd"] = -1

    received = int(upload["bytes_received"])
    expected = int(upload["total_size"])
    if received != expected:
        _remove_staged_upload_temp(upload.get("temp_path"))
        _set_state(ws, upload=None)
        _send_json(
            ws,
            {
                "type": "error",
                "message": f"Size mismatch: received {received}, expected {expected}",
            },
        )
        return

    user = state.get("user")
    handler = _mini_handler(tornado_app, user)
    username = "anonymous"
    if isinstance(user, dict):
        username = str(user.get("username") or username)
    elif user:
        username = str(user)

    from aird.handlers.base_handler import get_username_string_for_db

    try:
        username = get_username_string_for_db(handler) or username
    except Exception:
        pass

    app_ctx = tornado_app.settings.get("app_context")
    db_conn = app_ctx.db_conn if app_ctx is not None else tornado_app.settings.get("db_conn")
    quota = app_ctx.get_service("quota_service") if app_ctx is not None else None
    audit = app_ctx.get_service("audit_service") if app_ctx is not None else None

    success, status, message = finalize_upload_to_disk(
        upload_dir=upload["upload_dir"],
        filename=upload["filename"],
        temp_path=upload["temp_path"],
        user_root=upload["user_root"],
        username=username,
        db_conn=db_conn,
        quota_service=quota,
        audit_service=audit,
        remote_ip=None,
        upload_bytes=expected,
    )
    _set_state(ws, upload=None)
    if success:
        _send_json(ws, {"type": "upload_complete", "message": message})
    else:
        _remove_staged_upload_temp(upload.get("temp_path"))
        _send_json(ws, {"type": "error", "message": message, "status": status})


def _abort(ws, message: str | None) -> None:
    state = _get_state(ws)
    upload = state.get("upload")
    if not upload:
        if message:
            _send_json(ws, {"type": "error", "message": message})
        return
    fd = upload.get("fd")
    if fd is not None and int(fd) >= 0:
        try:
            os.close(int(fd))
        except OSError:
            pass
    _remove_staged_upload_temp(upload.get("temp_path"))
    _set_state(ws, upload=None)
    if message:
        _send_json(ws, {"type": "error", "message": message})
