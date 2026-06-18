"""Resumable HTTP uploads using Content-Range (files >= large-file threshold)."""

from __future__ import annotations

import json
import logging
import os
import secrets
import tempfile
import asyncio
from urllib.parse import unquote

import tornado.web

import aird.constants as constants_module
from aird.constants.file_ops import (
    ACCESS_DENIED,
    BAD_REQUEST,
    FILE_TOO_LARGE_TEMPLATE,
    FILE_UPLOAD_DISABLED,
    FILE_UPLOAD_DISABLED_ADMIN,
    UPLOAD_SAVE_FAILED,
)
from aird.core.http_range import (
    ByteRange,
    merge_ranges,
    parse_content_range,
    ranges_cover_file,
    ranges_to_json,
)
from aird.db.ranged_uploads import create_session, delete_session, get_session, update_ranges
from aird.handlers.base_handler import (
    BaseHandler,
    get_user_root,
    require_action,
    require_modify_access,
)
from aird.handlers.constants import DB_UNAVAILABLE_SHORT
from aird.handlers.file_op_handlers import finalize_upload_to_disk, _query_arg
from aird.utils.util import is_feature_enabled

logger = logging.getLogger(__name__)


def _write_range_sync(temp_path: str, start: int, data: bytes) -> None:
    with open(temp_path, "r+b") as fh:
        fh.seek(start)
        fh.write(data)


class RangedUploadSessionHandler(BaseHandler):
    """Create a ranged upload session (POST)."""

    @tornado.web.authenticated
    @require_action("file.write")
    @require_modify_access()
    async def post(self):
        if not self.require_feature("file_upload", True, body=FILE_UPLOAD_DISABLED_ADMIN):
            return
        if not is_feature_enabled("file_upload", True):
            self.set_status(403)
            self.write({"error": FILE_UPLOAD_DISABLED})
            return
        try:
            body = json.loads(self.request.body or b"{}")
        except json.JSONDecodeError:
            self.set_status(400)
            self.write({"error": BAD_REQUEST})
            return

        upload_dir = unquote(str(body.get("upload_dir") or ""))
        filename = unquote(str(body.get("filename") or ""))
        total_size = body.get("total_size")

        if not filename or not isinstance(total_size, int) or total_size < 0:
            self.set_status(400)
            self.write({"error": "filename and total_size are required"})
            return
        if total_size > constants_module.MAX_FILE_SIZE:
            limit_mb = constants_module.UPLOAD_CONFIG.get("max_file_size_mb", 512)
            self.set_status(413)
            self.write(
                {"error": FILE_TOO_LARGE_TEMPLATE.format(limit_mb=limit_mb)}
            )
            return
        if total_size < constants_module.LARGE_FILE_THRESHOLD_BYTES:
            self.set_status(400)
            self.write(
                {
                    "error": "Use POST /upload for files under the large-file threshold",
                    "threshold_bytes": constants_module.LARGE_FILE_THRESHOLD_BYTES,
                }
            )
            return

        if self.db_conn is None:
            self.set_status(500)
            self.write({"error": DB_UNAVAILABLE_SHORT})
            return

        session_id = secrets.token_urlsafe(16)
        fd, temp_path = tempfile.mkstemp(prefix="aird_range_")
        os.close(fd)
        try:
            await asyncio.to_thread(os.truncate, temp_path, total_size)
        except OSError:
            try:
                os.remove(temp_path)
            except OSError:
                pass
            self.set_status(500)
            self.write({"error": UPLOAD_SAVE_FAILED})
            return

        create_session(
            self.db_conn,
            session_id=session_id,
            username=self.get_display_username(),
            upload_dir=upload_dir,
            filename=filename,
            temp_path=temp_path,
            total_size=total_size,
        )
        self.set_status(201)
        self.write(
            {
                "upload_id": session_id,
                "total_size": total_size,
                "chunk_bytes": constants_module.RANGE_CHUNK_BYTES,
            }
        )


class RangedUploadChunkHandler(BaseHandler):
    """PUT a byte range into an upload session."""

    def check_xsrf_cookie(self) -> None:
        cookie_token = self.get_cookie("_xsrf")
        if not cookie_token:
            raise tornado.web.HTTPError(403, "'_xsrf' cookie missing")
        provided = self.request.headers.get("X-XSRFToken")
        if not provided:
            provided = _query_arg(self.request.arguments, "_xsrf")
        if not provided or not secrets.compare_digest(provided, cookie_token):
            raise tornado.web.HTTPError(403, "XSRF validation failed")

    @tornado.web.authenticated
    @require_action("file.write")
    @require_modify_access()
    async def put(self, upload_id: str):
        if not self.require_feature("file_upload", True, body=FILE_UPLOAD_DISABLED_ADMIN):
            return
        if self.db_conn is None:
            self.set_status(500)
            self.write({"error": DB_UNAVAILABLE_SHORT})
            return

        session = get_session(self.db_conn, upload_id)
        if not session:
            self.set_status(404)
            self.write({"error": "Upload session not found"})
            return
        if session["username"] != self.get_display_username():
            self.set_status(403)
            self.write({"error": ACCESS_DENIED})
            return

        parsed = parse_content_range(self.request.headers.get("Content-Range"))
        if not parsed:
            self.set_status(400)
            self.write({"error": "Content-Range header required"})
            return
        start, end, total = parsed
        if total is not None and total != session["total_size"]:
            self.set_status(400)
            self.write({"error": "Content-Range total does not match session"})
            return

        expected_len = end - start + 1
        body = self.request.body or b""
        if len(body) != expected_len:
            self.set_status(400)
            self.write(
                {
                    "error": f"Body length {len(body)} does not match range length {expected_len}",
                }
            )
            return
        if end >= session["total_size"]:
            self.set_status(416)
            self.write({"error": "Range beyond file size"})
            return

        temp_path = session["temp_path"]
        try:
            await asyncio.to_thread(_write_range_sync, temp_path, start, body)
        except OSError:
            logger.exception("Ranged upload write failed")
            self.set_status(500)
            self.write({"error": UPLOAD_SAVE_FAILED})
            return

        new_ranges = merge_ranges(session["ranges"] + [ByteRange(start, end)])
        update_ranges(self.db_conn, upload_id, new_ranges)

        if ranges_cover_file(new_ranges, session["total_size"]):
            success, status, message = await asyncio.to_thread(
                finalize_upload_to_disk,
                upload_dir=session["upload_dir"],
                filename=session["filename"],
                temp_path=temp_path,
                user_root=get_user_root(self),
                username=self.get_display_username(),
                db_conn=self.db_conn,
                quota_service=self.get_service("quota_service"),
                audit_service=self.get_service("audit_service"),
                remote_ip=self.request.remote_ip,
                upload_bytes=session["total_size"],
            )
            delete_session(self.db_conn, upload_id)
            if not success:
                self.set_status(status)
                self.write({"error": message})
                return
            self.set_status(201)
            self.write({"status": "complete", "message": message})
            return

        self.set_status(200)
        self.write(
            {
                "status": "chunk_received",
                "ranges": ranges_to_json(new_ranges),
                "total_size": session["total_size"],
            }
        )


class RangedUploadStatusHandler(BaseHandler):
    """GET upload session status (missing ranges for resume)."""

    @tornado.web.authenticated
    async def get(self, upload_id: str):
        if self.db_conn is None:
            self.set_status(500)
            self.write({"error": DB_UNAVAILABLE_SHORT})
            return
        session = get_session(self.db_conn, upload_id)
        if not session:
            self.set_status(404)
            self.write({"error": "Upload session not found"})
            return
        if session["username"] != self.get_display_username():
            self.set_status(403)
            self.write({"error": ACCESS_DENIED})
            return
        self.write(
            {
                "upload_id": upload_id,
                "total_size": session["total_size"],
                "ranges": ranges_to_json(session["ranges"]),
                "complete": ranges_cover_file(session["ranges"], session["total_size"]),
            }
        )
