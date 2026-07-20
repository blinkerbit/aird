"""Resumable HTTP uploads using Content-Range (files >= large-file threshold)."""

from __future__ import annotations

import json
import logging
import os
import secrets
import tempfile
import asyncio
import threading
from collections import deque
from urllib.parse import unquote

import aiofiles
import tornado.web

import aird.constants as constants_module
from aird.constants.file_ops import (
    ACCESS_DENIED,
    BAD_REQUEST,
    FILE_TOO_LARGE_TEMPLATE,
    FILE_UPLOAD_DISABLED,
    FILE_UPLOAD_DISABLED_ADMIN,
    UPLOAD_DISK_FULL,
    UPLOAD_SAVE_FAILED,
)
from aird.core.http_range import (
    ByteRange,
    merge_ranges,
    parse_content_range,
    ranges_cover_file,
    ranges_to_json,
)
from aird.db.ranged_uploads import (
    count_active_sessions,
    create_session,
    delete_session,
    get_session,
    update_ranges,
)
from aird.handlers.base_handler import (
    BaseHandler,
    get_user_root,
    require_action,
    require_modify_access,
)
from aird.handlers.constants import DB_UNAVAILABLE_SHORT
from aird.handlers.file_op_handlers import finalize_upload_to_disk, _query_arg
from aird.utils.util import is_feature_enabled
from aird.core.rate_limit import TransferRateLimiter

logger = logging.getLogger(__name__)

_SESSION_NOT_FOUND = "Upload session not found"
_MAX_ACTIVE_SESSIONS_PER_USER = 5
_session_locks: dict[str, asyncio.Lock] = {}
_session_registry_lock = threading.Lock()
_active_chunk_streams: dict[str, int] = {}
_active_chunk_streams_lock = threading.Lock()


def _session_lock(upload_id: str) -> asyncio.Lock:
    with _session_registry_lock:
        lock = _session_locks.get(upload_id)
        if lock is None:
            lock = asyncio.Lock()
            _session_locks[upload_id] = lock
        return lock


def _release_session_lock(upload_id: str) -> None:
    with _session_registry_lock:
        _session_locks.pop(upload_id, None)


def _try_acquire_chunk_stream(username: str, limit: int) -> bool:
    with _active_chunk_streams_lock:
        active = _active_chunk_streams.get(username, 0)
        if active >= max(1, limit):
            return False
        _active_chunk_streams[username] = active + 1
        return True


def _release_chunk_stream(username: str | None) -> None:
    if not username:
        return
    with _active_chunk_streams_lock:
        active = _active_chunk_streams.get(username, 0)
        if active <= 1:
            _active_chunk_streams.pop(username, None)
        else:
            _active_chunk_streams[username] = active - 1


def _write_range_sync(temp_path: str, start: int, data: bytes) -> None:
    """Write one chunk at *start* into the session temp file (in-place assembly).

    Chunks are written directly at their byte offset — no separate part files and
    no concat/copy step at finalize (just truncate + rename). Safe for parallel
    writers when ranges do not overlap (enforced by Content-Range).
    """
    pwrite = getattr(os, "pwrite", None)
    if pwrite is not None:
        fd = os.open(temp_path, os.O_RDWR)
        try:
            _pwrite_all(fd, data, start)
        finally:
            os.close(fd)
        return
    with open(temp_path, "r+b") as fh:
        fh.seek(start)
        fh.write(data)


def _pwrite_all(fd: int, data: bytes, offset: int) -> None:
    view = memoryview(data)
    written_total = 0
    while written_total < len(view):
        written = os.pwrite(fd, view[written_total:], offset + written_total)
        if written <= 0:
            raise OSError("Short pwrite while storing upload range")
        written_total += written


def _copy_range_file_sync(
    destination_path: str, source_path: str, start: int
) -> None:
    """Copy one request-body temp file into the session file at *start*."""
    pwrite = getattr(os, "pwrite", None)
    with open(source_path, "rb") as source:
        if pwrite is not None:
            fd = os.open(destination_path, os.O_RDWR)
            try:
                offset = start
                while True:
                    chunk = source.read(1024 * 1024)
                    if not chunk:
                        break
                    _pwrite_all(fd, chunk, offset)
                    offset += len(chunk)
            finally:
                os.close(fd)
            return
        with open(destination_path, "r+b") as destination:
            destination.seek(start)
            while True:
                chunk = source.read(1024 * 1024)
                if not chunk:
                    break
                destination.write(chunk)


def _ensure_upload_file_size_sync(temp_path: str, total_size: int) -> None:
    """Set exact file length once at finalize (cheap vs pre-allocating total_size)."""
    size = os.path.getsize(temp_path)
    if size != total_size:
        os.truncate(temp_path, total_size)


def _is_disk_full_error(exc: OSError) -> bool:
    enospc = getattr(os, "ENOSPC", 28)
    return exc.errno in (enospc, 28)


def _upload_storage_response(exc: OSError) -> tuple[int, str]:
    if _is_disk_full_error(exc):
        return 507, UPLOAD_DISK_FULL
    return 500, UPLOAD_SAVE_FAILED


def _chunk_put_error(
    handler: BaseHandler, status: int, error: str
) -> None:
    handler.set_status(status)
    handler.write({"error": error})


def _validate_chunk_put_request(
    handler: BaseHandler,
    session: dict,
    parsed: tuple[int, int, int | None] | None,
    body: bytes | int,
) -> tuple[int, int] | None:
    """Return (start, end) when valid; otherwise write error response and return None."""
    if not parsed:
        _chunk_put_error(handler, 400, "Content-Range header required")
        return None
    start, end, total = parsed
    if total is not None and total != session["total_size"]:
        _chunk_put_error(handler, 400, "Content-Range total does not match session")
        return None

    expected_len = end - start + 1
    max_chunk = max(int(session.get("chunk_bytes") or 0), 4 * 1024 * 1024)
    if expected_len > max_chunk:
        max_mb = max_chunk // (1024 * 1024)
        chunk_mb = expected_len // (1024 * 1024)
        handler.set_status(413)
        handler.write(
            {
                "error": (
                    f"Chunk too large ({chunk_mb} MB > {max_mb} MB server limit). "
                    "Admin → Upload settings → lower HTTP chunk (MB) or redeploy "
                    "so server matches client."
                ),
            }
        )
        return None
    body_length = body if isinstance(body, int) else len(body)
    if body_length != expected_len:
        handler.set_status(400)
        handler.write(
            {
                "error": (
                    f"Body length {body_length} does not match range length {expected_len}"
                ),
            }
        )
        return None
    if end >= session["total_size"]:
        _chunk_put_error(handler, 416, "Range beyond file size")
        return None
    return start, end


async def _finalize_ranged_upload_if_complete(
    handler: BaseHandler,
    upload_id: str,
    session: dict,
    temp_path: str,
    new_ranges: list,
) -> bool:
    """Finalize when all ranges received. Return True if response was sent."""
    if not ranges_cover_file(new_ranges, session["total_size"]):
        return False
    try:
        await asyncio.to_thread(
            _ensure_upload_file_size_sync,
            temp_path,
            session["total_size"],
        )
    except OSError:
        logger.exception("Ranged upload finalize size check failed")
        handler.set_status(500)
        handler.write({"error": UPLOAD_SAVE_FAILED})
        return True
    success, status, message = await asyncio.to_thread(
        finalize_upload_to_disk,
        upload_dir=session["upload_dir"],
        filename=session["filename"],
        temp_path=temp_path,
        user_root=get_user_root(handler),
        username=handler.get_display_username(),
        db_conn=handler.db_conn,
        quota_service=handler.get_service("quota_service"),
        audit_service=handler.get_service("audit_service"),
        remote_ip=handler.request.remote_ip,
        upload_bytes=session["total_size"],
    )
    delete_session(handler.db_conn, upload_id)
    _release_session_lock(upload_id)
    if not success:
        handler.set_status(status)
        handler.write({"error": message})
        return True
    handler.set_status(201)
    handler.write({"status": "complete", "message": message})
    return True


class RangedUploadSessionHandler(BaseHandler):
    """Create a ranged upload session (POST)."""

    @tornado.web.authenticated
    @require_action("file.write")
    @require_modify_access()
    async def post(self):
        self.sync_upload_config_from_db()
        if not self.require_feature("file_upload", True, body=FILE_UPLOAD_DISABLED_ADMIN):
            return
        if not is_feature_enabled("file_upload", True):
            self.set_status(403)
            self.write({"error": FILE_UPLOAD_DISABLED})
            return
        strategy = constants_module.get_effective_transfer_strategy()
        if strategy["uploadTransport"] == "stream":
            self.set_status(409)
            self.write(
                {
                    "error": (
                        "Ranged uploads are disabled for the WireGuard profile; "
                        "use POST /upload"
                    )
                }
            )
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
        username = self.get_display_username()
        if count_active_sessions(self.db_conn, username) >= _MAX_ACTIVE_SESSIONS_PER_USER:
            self.set_status(429)
            self.set_header("Retry-After", "30")
            self.write({"error": "Too many active upload sessions"})
            return

        session_id = secrets.token_urlsafe(16)
        fd, temp_path = tempfile.mkstemp(prefix="aird_range_")
        os.close(fd)
        # Empty temp file; ranges extend it on write (no upfront truncate of total_size).

        create_session(
            self.db_conn,
            session_id=session_id,
            username=self.get_display_username(),
            upload_dir=upload_dir,
            filename=filename,
            temp_path=temp_path,
            total_size=total_size,
            transfer_profile=strategy["profile"],
            chunk_bytes=int(strategy["rangeChunkBytes"]),
        )
        self.set_status(201)
        self.write(
            {
                "upload_id": session_id,
                "total_size": total_size,
                "chunk_bytes": int(strategy["rangeChunkBytes"]),
                "transfer_profile": strategy["profile"],
            }
        )


@tornado.web.stream_request_body
class RangedUploadChunkHandler(BaseHandler):
    """PUT a byte range into an upload session."""

    async def prepare(self):
        BaseHandler.prepare(self)
        self.sync_upload_config_from_db()
        self._request_temp_path = None
        self._request_file = None
        self._request_buffer = deque()
        self._request_writer_task = None
        self._request_writing = False
        self._request_write_error = None
        self._request_bytes = 0
        self._chunk_slot_user = None
        if self.request.method != "PUT":
            return
        current_user = self.get_current_user()
        username = self.get_display_username() if current_user else None
        upload_id = self.path_args[0] if getattr(self, "path_args", None) else None
        session = (
            get_session(self.db_conn, upload_id)
            if self.db_conn is not None and upload_id
            else None
        )
        strategy = constants_module.get_effective_transfer_strategy()
        request_limit = (
            int(session["chunk_bytes"])
            if session is not None
            else int(strategy["rangeChunkBytes"])
        )
        try:
            self.request.connection.set_max_body_size(
                request_limit + (1024 * 1024)
            )
        except (AttributeError, RuntimeError):
            pass
        if session and session["username"] == username:
            preset = constants_module.TRANSFER_PROFILE_PRESETS.get(
                session["transfer_profile"],
                constants_module.TRANSFER_PROFILE_PRESETS["open"],
            )
            limit = int(preset["range_upload_concurrency"])
            if not _try_acquire_chunk_stream(username, limit):
                self.set_header("Retry-After", "5")
                raise tornado.web.HTTPError(
                    429, reason="Too many concurrent upload chunks"
                )
            self._chunk_slot_user = username
        fd, self._request_temp_path = tempfile.mkstemp(prefix="aird_range_request_")
        os.close(fd)
        self._request_file = await aiofiles.open(self._request_temp_path, "wb")

    def data_received(self, chunk: bytes) -> None:
        if self._request_write_error is not None:
            return
        self._request_bytes += len(chunk)
        self._request_buffer.append(chunk)
        if not self._request_writing:
            self._request_writing = True
            self._request_writer_task = asyncio.create_task(
                self._drain_request_buffer()
            )

    async def _drain_request_buffer(self) -> None:
        try:
            while self._request_buffer:
                await self._request_file.write(self._request_buffer.popleft())
        except OSError as exc:
            self._request_write_error = exc
            logger.warning("Ranged upload request staging write failed: %s", exc)
        finally:
            self._request_writing = False
            if self._request_buffer and self._request_write_error is None:
                self._request_writing = True
                self._request_writer_task = asyncio.create_task(
                    self._drain_request_buffer()
                )

    async def _finalize_request_body(self) -> None:
        while self._request_writer_task is not None:
            task = self._request_writer_task
            self._request_writer_task = None
            await task
        if self._request_file is not None:
            await self._request_file.flush()
            await self._request_file.close()
            self._request_file = None

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
        self.sync_upload_config_from_db()
        if not self.require_feature("file_upload", True, body=FILE_UPLOAD_DISABLED_ADMIN):
            return
        if self.db_conn is None:
            self.set_status(500)
            self.write({"error": DB_UNAVAILABLE_SHORT})
            return

        user_key = self.get_display_username() or self.request.remote_ip or "anonymous"

        session = get_session(self.db_conn, upload_id)
        if not session:
            self.set_status(404)
            self.write({"error": _SESSION_NOT_FOUND})
            return
        if session["username"] != self.get_display_username():
            self.set_status(403)
            self.write({"error": ACCESS_DENIED})
            return

        parsed = parse_content_range(self.request.headers.get("Content-Range"))
        streamed_request = hasattr(self, "_request_bytes")
        if streamed_request:
            await self._finalize_request_body()
            if self._request_write_error is not None:
                status, message = _upload_storage_response(self._request_write_error)
                self.set_status(status)
                self.write({"error": message})
                return
            body_length = self._request_bytes
        else:
            body_length = len(self.request.body or b"")
        validated = _validate_chunk_put_request(
            self, session, parsed, body_length
        )
        if validated is None:
            return
        start, end = validated

        await TransferRateLimiter.wait_for_bytes(
            user_key, body_length, direction="upload"
        )

        temp_path = session["temp_path"]
        try:
            if streamed_request:
                await asyncio.to_thread(
                    _copy_range_file_sync,
                    temp_path,
                    self._request_temp_path,
                    start,
                )
            else:
                await asyncio.to_thread(
                    _write_range_sync,
                    temp_path,
                    start,
                    self.request.body or b"",
                )
        except OSError as exc:
            logger.exception("Ranged upload write failed")
            status, message = _upload_storage_response(exc)
            self.set_status(status)
            self.write({"error": message})
            return

        lock = _session_lock(upload_id)
        async with lock:
            session = get_session(self.db_conn, upload_id)
            if not session:
                self.set_status(404)
                self.write({"error": _SESSION_NOT_FOUND})
                return

            new_ranges = merge_ranges(session["ranges"] + [ByteRange(start, end)])
            update_ranges(self.db_conn, upload_id, new_ranges)

            if await _finalize_ranged_upload_if_complete(
                self, upload_id, session, temp_path, new_ranges
            ):
                return

            self.set_status(200)
            self.write(
                {
                    "status": "chunk_received",
                    "ranges": ranges_to_json(new_ranges),
                    "total_size": session["total_size"],
                    "transfer_profile": session["transfer_profile"],
                    "chunk_bytes": session["chunk_bytes"],
                }
            )

    def on_finish(self) -> None:
        _release_chunk_stream(getattr(self, "_chunk_slot_user", None))
        try:
            request_temp_path = getattr(self, "_request_temp_path", None)
            if request_temp_path and os.path.exists(request_temp_path):
                os.remove(request_temp_path)
        except OSError:
            logger.debug("Range request temp cleanup failed", exc_info=True)
        super().on_finish()


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
            self.write({"error": _SESSION_NOT_FOUND})
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
                "transfer_profile": session["transfer_profile"],
                "chunk_bytes": session["chunk_bytes"],
            }
        )
