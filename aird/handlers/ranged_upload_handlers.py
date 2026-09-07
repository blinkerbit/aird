"""Resumable HTTP uploads using Content-Range (files >= large-file threshold)."""

from __future__ import annotations

import json
import logging
import os
import secrets
import socket
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
    range_fully_covered,
    ranges_cover_file,
    ranges_to_json,
)
from aird.db.ranged_uploads import (
    count_active_sessions,
    create_session,
    delete_session,
    find_matching_session,
    get_session,
    list_reclaimable_sessions,
    update_ranges,
)
from aird.handlers.base_handler import (
    BaseHandler,
    get_user_root,
    get_username_string_for_db,
    require_action,
    require_modify_access,
)
from aird.core.browse_paths import mounts_for_username
from aird.handlers.constants import DB_UNAVAILABLE_SHORT
from aird.handlers.file_op_handlers import (
    _validate_upload_destination,
    finalize_upload_to_disk,
    _query_arg,
)
from aird.utils.util import is_feature_enabled
from aird.core.rate_limit import TransferRateLimiter

logger = logging.getLogger(__name__)

_SESSION_NOT_FOUND = "Upload session not found"
_UPLOAD_SESSION_TEMP_CLEANUP_FAILED = "Upload session temp cleanup failed"
# WireGuard/LAN can leave incomplete sessions after disconnects; allow enough
# headroom for resume + a multi-file queue without failing the first upload.
_MAX_ACTIVE_SESSIONS_PER_USER = 32


def _remove_session_temp(temp_path: str | None) -> None:
    if not temp_path:
        return
    try:
        if os.path.exists(temp_path):
            os.remove(temp_path)
    except OSError:
        logger.debug(_UPLOAD_SESSION_TEMP_CLEANUP_FAILED, exc_info=True)


def _reclaim_sessions_for_user(conn, username: str, need: int = 1) -> int:
    """Free empty (or oldest) incomplete sessions so a new upload can start."""
    reclaimed = 0
    for empty_only in (True, False):
        if reclaimed >= need:
            break
        for session in list_reclaimable_sessions(conn, username, empty_only=empty_only):
            if reclaimed >= need:
                break
            _remove_session_temp(session.get("temp_path"))
            delete_session(conn, session["id"])
            _release_session_lock(session["id"])
            reclaimed += 1
    return reclaimed


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


def _tune_chunk_upload_socket(handler: BaseHandler) -> None:
    try:
        stream = getattr(handler.request.connection, "stream", None)
        sock = getattr(stream, "socket", None) if stream is not None else None
        if sock is not None:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 8 * 1024 * 1024)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 8 * 1024 * 1024)
    except OSError:
        pass


def _acquire_chunk_upload_slot(
    handler: BaseHandler, session: dict | None, username: str | None
) -> None:
    if not session or session["username"] != username:
        return
    preset = constants_module.TRANSFER_PROFILE_PRESETS.get(
        session["transfer_profile"],
        constants_module.TRANSFER_PROFILE_PRESETS["open"],
    )
    limit = int(preset["range_upload_concurrency"]) * max(
        1, int(preset.get("range_pipeline_depth", 1))
    )
    limit += 2
    if not _try_acquire_chunk_stream(username, limit):
        handler.set_header("Retry-After", "5")
        raise tornado.web.HTTPError(429, reason="Too many concurrent upload chunks")
    handler._chunk_slot_user = username


async def _write_chunk_payload(
    handler: BaseHandler,
    *,
    session: dict,
    start: int,
    streamed_request: bool,
) -> OSError | None:
    temp_path = session["temp_path"]
    try:
        if streamed_request and getattr(handler, "_direct_path", None):
            return None
        if streamed_request:
            await asyncio.to_thread(
                _copy_range_file_sync,
                temp_path,
                handler._request_temp_path,
                start,
            )
        else:
            await asyncio.to_thread(
                _write_range_sync,
                temp_path,
                start,
                handler.request.body or b"",
            )
    except OSError as exc:
        logger.exception("Ranged upload write failed")
        status, message = _upload_storage_response(exc)
        handler.set_status(status)
        handler.write({"error": message})
        return exc
    return None


def _chunk_received_response(
    handler: BaseHandler, session: dict, new_ranges: list
) -> None:
    handler.set_status(200)
    handler.write(
        {
            "status": "chunk_received",
            "ranges": ranges_to_json(new_ranges),
            "total_size": session["total_size"],
            "transfer_profile": session["transfer_profile"],
            "chunk_bytes": session["chunk_bytes"],
        }
    )


async def _chunk_put_body_length(
    handler: BaseHandler, streamed_request: bool
) -> int | None:
    if not streamed_request:
        return len(handler.request.body or b"")
    await handler._finalize_request_body()
    if handler._request_write_error is not None:
        status, message = _upload_storage_response(handler._request_write_error)
        handler.set_status(status)
        handler.write({"error": message})
        return None
    return handler._request_bytes


async def _commit_uploaded_chunk_range(
    handler: BaseHandler,
    upload_id: str,
    session: dict,
    start: int,
    end: int,
) -> None:
    lock = _session_lock(upload_id)
    async with lock:
        session = get_session(handler.db_conn, upload_id)
        if not session:
            handler.set_status(404)
            handler.write({"error": _SESSION_NOT_FOUND})
            return
        new_ranges = merge_ranges(session["ranges"] + [ByteRange(start, end)])
        update_ranges(handler.db_conn, upload_id, new_ranges)
        if await _finalize_ranged_upload_if_complete(
            handler, upload_id, session, session["temp_path"], new_ranges
        ):
            return
        _chunk_received_response(handler, session, new_ranges)


def _parse_ranged_session_request(
    handler: BaseHandler,
) -> tuple[str, str, int] | None:
    try:
        body = json.loads(handler.request.body or b"{}")
    except json.JSONDecodeError:
        handler.set_status(400)
        handler.write({"error": BAD_REQUEST})
        return None
    upload_dir = unquote(str(body.get("upload_dir") or ""))
    filename = unquote(str(body.get("filename") or ""))
    total_size = body.get("total_size")
    if not filename or not isinstance(total_size, int) or total_size < 0:
        handler.set_status(400)
        handler.write({"error": "filename and total_size are required"})
        return None
    if total_size > constants_module.MAX_FILE_SIZE:
        limit_mb = constants_module.UPLOAD_CONFIG.get("max_file_size_mb", 512)
        handler.set_status(413)
        handler.write({"error": FILE_TOO_LARGE_TEMPLATE.format(limit_mb=limit_mb)})
        return None
    if total_size < constants_module.LARGE_FILE_THRESHOLD_BYTES:
        handler.set_status(400)
        handler.write(
            {
                "error": "Use POST /upload for files under the large-file threshold",
                "threshold_bytes": constants_module.LARGE_FILE_THRESHOLD_BYTES,
            }
        )
        return None
    return upload_dir, filename, total_size


def _ensure_ranged_session_capacity(handler: BaseHandler, username: str) -> bool:
    active = count_active_sessions(handler.db_conn, username)
    if active >= _MAX_ACTIVE_SESSIONS_PER_USER:
        _reclaim_sessions_for_user(
            handler.db_conn,
            username,
            need=active - _MAX_ACTIVE_SESSIONS_PER_USER + 1,
        )
    if count_active_sessions(handler.db_conn, username) >= _MAX_ACTIVE_SESSIONS_PER_USER:
        handler.set_status(429)
        handler.set_header("Retry-After", "30")
        handler.write({"error": "Too many active upload sessions"})
        return False
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
        parsed = _parse_ranged_session_request(self)
        if parsed is None:
            return
        upload_dir, filename, total_size = parsed

        if self.db_conn is None:
            self.set_status(500)
            self.write({"error": DB_UNAVAILABLE_SHORT})
            return
        username = self.get_display_username()

        # Reuse an existing incomplete session for the same file (resume / retry).
        existing = find_matching_session(
            self.db_conn,
            username=username,
            upload_dir=upload_dir,
            filename=filename,
            total_size=total_size,
        )
        if existing is not None:
            self.set_status(200)
            self.write(
                {
                    "upload_id": existing["id"],
                    "total_size": existing["total_size"],
                    "chunk_bytes": int(existing["chunk_bytes"]),
                    "transfer_profile": existing["transfer_profile"],
                    "resumed": True,
                }
            )
            return

        if not _ensure_ranged_session_capacity(self, username):
            return

        session_id = secrets.token_urlsafe(16)
        user_root = get_user_root(self)
        final_path_abs, upload_err = _validate_upload_destination(
            upload_dir,
            filename,
            user_root,
            mounts_for_username(self.db_conn, get_username_string_for_db(self) or ""),
        )
        if upload_err is not None:
            self.set_status(upload_err[0])
            self.write({"error": upload_err[1]})
            return
        dest_dir = os.path.dirname(final_path_abs) or user_root
        os.makedirs(dest_dir, exist_ok=True)
        fd, temp_path = tempfile.mkstemp(prefix=".aird_range_")
        # Do NOT pre-truncate to total_size on Windows — ftruncate zero-fills and
        # stalled a 3.8 GiB session create for ~30s before the first byte uploaded.
        # Linux sparse truncate is fine for parallel seeks; keep it there only.
        try:
            if total_size > 0 and os.name != "nt":
                os.ftruncate(fd, total_size)
        finally:
            os.close(fd)

        strategy = constants_module.get_effective_transfer_strategy()
        create_session(
            self.db_conn,
            session_id=session_id,
            username=username,
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
        self._direct_path = None
        self._direct_offset = None
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
        _tune_chunk_upload_socket(self)
        if session and session["username"] == username:
            _acquire_chunk_upload_slot(self, session, username)
        # Prefer writing straight into the session file at Content-Range offset
        # (one disk write). Fall back to a staging temp when range is missing.
        parsed = parse_content_range(self.request.headers.get("Content-Range"))
        if session is not None and parsed is not None:
            self._direct_path = session["temp_path"]
            self._direct_offset = parsed[0]
        else:
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
                if getattr(self, "_direct_path", None) is not None and getattr(
                    self, "_direct_offset", None
                ) is not None:
                    # Coalesce TCP fragments into larger disk writes (fewer open/seek cycles).
                    parts: list[bytes] = []
                    total = 0
                    while self._request_buffer and total < (8 * 1024 * 1024):
                        part = self._request_buffer.popleft()
                        parts.append(part)
                        total += len(part)
                    data = parts[0] if len(parts) == 1 else b"".join(parts)
                    offset = self._direct_offset
                    self._direct_offset = offset + len(data)
                    await asyncio.to_thread(
                        _write_range_sync, self._direct_path, offset, data
                    )
                else:
                    await self._request_file.write(self._request_buffer.popleft())
        except OSError as exc:
            self._request_write_error = exc
            logger.warning("Ranged upload request write failed: %s", exc)
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
        body_length = await _chunk_put_body_length(self, streamed_request)
        if body_length is None:
            return
        validated = _validate_chunk_put_request(
            self, session, parsed, body_length
        )
        if validated is None:
            return
        start, end = validated

        # Idempotent retry: range already stored after a prior disconnect.
        if range_fully_covered(session["ranges"], start, end):
            if await _finalize_ranged_upload_if_complete(
                self, upload_id, session, session["temp_path"], session["ranges"]
            ):
                return
            _chunk_received_response(self, session, session["ranges"])
            return

        await TransferRateLimiter.wait_for_bytes(
            user_key, body_length, direction="upload"
        )

        if await _write_chunk_payload(
            self, session=session, start=start, streamed_request=streamed_request
        ) is not None:
            return

        await _commit_uploaded_chunk_range(self, upload_id, session, start, end)

    def on_finish(self) -> None:
        _release_chunk_stream(getattr(self, "_chunk_slot_user", None))
        try:
            request_temp_path = getattr(self, "_request_temp_path", None)
            if request_temp_path and os.path.exists(request_temp_path):
                os.remove(request_temp_path)
        except OSError:
            logger.debug("Range request temp cleanup failed", exc_info=True)
        super().on_finish()

    @tornado.web.authenticated
    @require_action("file.write")
    async def delete(self, upload_id: str):
        """Discard an incomplete ranged upload session (explicit cancel)."""
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
        temp_path = session.get("temp_path")
        delete_session(self.db_conn, upload_id)
        _release_session_lock(upload_id)
        if temp_path:
            try:
                if os.path.exists(temp_path):
                    os.remove(temp_path)
            except OSError:
                logger.debug(_UPLOAD_SESSION_TEMP_CLEANUP_FAILED, exc_info=True)
        self.set_status(200)
        self.write({"status": "deleted"})


class RangedUploadStatusHandler(BaseHandler):
    """GET upload session status; DELETE discards an incomplete session."""

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

    @tornado.web.authenticated
    @require_action("file.write")
    async def delete(self, upload_id: str):
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
        temp_path = session.get("temp_path")
        delete_session(self.db_conn, upload_id)
        _release_session_lock(upload_id)
        if temp_path:
            try:
                if os.path.exists(temp_path):
                    os.remove(temp_path)
            except OSError:
                logger.debug(_UPLOAD_SESSION_TEMP_CLEANUP_FAILED, exc_info=True)
        self.set_status(200)
        self.write({"status": "deleted"})
