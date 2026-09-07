import tornado.web
import os
import shutil
import socket
import tempfile
import json
import logging
import pathlib
import re
import secrets
from collections import deque
from urllib.parse import unquote
import asyncio
import aiofiles
from typing import Protocol, Callable, Any

from aird.handlers.base_handler import (
    BaseHandler,
    XSRFTokenMixin,
    require_action,
    require_modify_access,
    get_user_root,
    get_username_string_for_db,
    resolve_handler_rel,
)
from aird.core.transfer_native import try_start_native_socket_upload
from aird.core.browse_paths import mounts_for_username, write_blocked_reason
from aird.core.mmap_handler import MMapFileHandler
from aird.core.zip_download import ZipDownloadError, build_zip_file, collect_zip_entries
from aird.utils.util import sanitize_cloud_filename, is_feature_enabled, get_file_size_safe
from aird.constants.input_limits import (
    EDIT_JSON_BODY_MAX_BYTES,
    MAX_BULK_JSON_BYTES,
    MAX_BULK_PATHS,
    REL_PATH_MAX_LEN,
    SHARE_ID_MAX_LEN,
)
from aird.core.security import (  # noqa: F401
    is_within_root,
    is_valid_websocket_origin,
    join_path,
)
import aird.constants as constants_module
from aird.constants.file_ops import (
    ACCESS_DENIED,
    ACCESS_DENIED_LOWER,
    ACCESS_DENIED_PATH,
    ACCESS_DENIED_SHORT,
    ACCESS_DENIED_WITH_PERIOD,
    BAD_REQUEST,
    CLOUD_UPLOAD_FAILED,
    CONFLICT_EXISTS,
    COPY_DISABLED,
    COPY_FAILED,
    DATABASE_UNAVAILABLE,
    DESTINATION_EXISTS,
    FILE_DELETE_DISABLED,
    FILE_DELETE_DISABLED_LOWER,
    FILE_EDIT_DISABLED,
    FILE_NOT_FOUND,
    FILE_OR_FOLDER_NOT_FOUND,
    FILE_RENAME_DISABLED,
    FILE_SAVE_ERROR,
    FILE_SAVED_SUCCESSFULLY,
    FILE_TOO_LARGE,
    FILE_TOO_LARGE_TEMPLATE,
    FILE_UPLOAD_DISABLED,
    FILE_UPLOAD_DISABLED_ADMIN,
    FILENAME_TOO_LONG,
    FOLDER_CREATE_DISABLED,
    FOLDER_CREATE_FAILED,
    FOLDER_DELETE_DISABLED,
    FOLDER_DELETE_DISABLED_LOWER,
    FOLDER_NAME_TOO_LONG,
    FOLDER_NOT_EMPTY,
    INVALID_FILENAME,
    INVALID_FOLDER_NAME,
    INVALID_JSON,
    INVALID_JSON_REQUEST,
    INVALID_PATH,
    INVALID_REQUEST_PATH_AND_NAME,
    INVALID_REQUEST_PATH_DEST,
    MOVE_DISABLED,
    MOVE_FAILED,
    NO_FILE_UPLOADED,
    NOT_FOUND_LOWER,
    PATHS_REQUIRED,
    PROVIDER_NOT_CONFIGURED,
    RENAME_FAILED,
    SHARE_ID_REQUIRED,
    SHARE_NOT_FOUND,
    SOURCE_NOT_FOUND,
    UNSUPPORTED_ACTION,
    UNSUPPORTED_FILE_TYPE,
    UPDATE_FAILED,
    UPLOAD_SAVE_FAILED,
    UPLOAD_SUCCESSFUL,
    MISSING_UPLOAD_FILENAME_HEADER,
)
from aird.config import (
    ALLOWED_UPLOAD_EXTENSIONS,
    CLOUD_MANAGER,
)
from aird.cloud import CloudManager
from io import BytesIO

HEADER_APPLICATION_JSON = "application/json"
FILES_URL_STRING = "/files/"


def _handler_mounts(handler) -> list[dict]:
    return mounts_for_username(handler.db_conn, get_username_string_for_db(handler) or "")


def _deny_write_rel(handler, *rels, as_target: bool = False) -> bool:
    mounts = _handler_mounts(handler)
    for rel in rels:
        if write_blocked_reason(rel, mounts, as_target=as_target):
            handler.set_status(403)
            handler.write(ACCESS_DENIED)
            return True
    return False


# ---------------------------------------------------------------------------
# Helpers for upload validation (reduce cognitive complexity)
# ---------------------------------------------------------------------------


def _resolve_upload_dir(
    upload_dir: str, root_dir: str, mounts: list | None
) -> tuple[str, str | None, str | None, tuple[int, str] | None]:
    from aird.core.browse_paths import resolve_rel

    rel = (upload_dir or "").strip().strip("/")
    if not rel:
        safe_dir_abs = os.path.realpath(root_dir)
        confine = os.path.realpath(root_dir)
        return rel, safe_dir_abs, confine, None
    resolved, confine = resolve_rel(root_dir, rel, mounts or [])
    if not resolved or not confine:
        return rel, None, None, (403, ACCESS_DENIED_PATH)
    safe_dir_abs = os.path.realpath(resolved)
    confine = os.path.realpath(confine)
    if not is_within_root(safe_dir_abs, confine):
        return rel, None, None, (403, ACCESS_DENIED_PATH)
    return rel, safe_dir_abs, confine, None


def _validate_upload_filename(filename: str) -> tuple[str | None, tuple[int, str] | None]:
    safe_filename = os.path.basename(filename)
    if not safe_filename or safe_filename in (".", ".."):
        return None, (400, INVALID_FILENAME)
    allow_all = constants_module.UPLOAD_CONFIG.get("allow_all_file_types", 0)
    if not allow_all:
        file_ext = os.path.splitext(safe_filename)[1].lower()
        allowed_set = (
            getattr(constants_module, "UPLOAD_ALLOWED_EXTENSIONS", None)
            or ALLOWED_UPLOAD_EXTENSIONS
        )
        if file_ext not in allowed_set:
            return None, (415, UNSUPPORTED_FILE_TYPE)
    if len(safe_filename) > 255:
        return None, (400, FILENAME_TOO_LONG)
    return safe_filename, None


def _validate_upload_destination(upload_dir, filename, root_dir, mounts=None):
    """Validate upload dir and filename. Return (final_path_abs, None) or (None, (status, message))."""
    rel, safe_dir_abs, confine, dir_err = _resolve_upload_dir(
        upload_dir, root_dir, mounts
    )
    if dir_err is not None:
        return (None, dir_err)
    if safe_dir_abs is None or confine is None:
        return (None, (403, ACCESS_DENIED_PATH))
    if not is_within_root(safe_dir_abs, confine):
        return (None, (403, ACCESS_DENIED_PATH))
    safe_filename, name_err = _validate_upload_filename(filename)
    if name_err is not None:
        return (None, name_err)
    final_path_abs = os.path.realpath(os.path.join(safe_dir_abs, safe_filename))
    if not is_within_root(final_path_abs, safe_dir_abs):
        return (None, (403, ACCESS_DENIED_PATH))
    if write_blocked_reason(rel, mounts or []):
        return (None, (403, ACCESS_DENIED_PATH))
    return (final_path_abs, None)


def finalize_upload_to_disk(
    *,
    upload_dir: str,
    filename: str,
    temp_path: str,
    user_root: str,
    username: str,
    db_conn,
    quota_service,
    audit_service,
    remote_ip: str | None,
    upload_bytes: int,
) -> tuple[bool, int, str]:
    """Move a staged upload file into the user's tree. Returns (ok, status, message)."""
    if quota_service and is_feature_enabled("storage_quotas", False):
        quota = quota_service.get_quota(db_conn, username)
        if (
            quota["quota_bytes"] is not None
            and quota["used_bytes"] + upload_bytes > quota["quota_bytes"]
        ):
            return (False, 413, "Storage quota exceeded")

    final_path_abs, upload_err = _validate_upload_destination(
        upload_dir,
        filename,
        user_root,
        mounts_for_username(db_conn, username) if db_conn else None,
    )
    if upload_err is not None:
        return (False, upload_err[0], upload_err[1])

    try:
        actual_bytes = os.path.getsize(temp_path)
    except OSError:
        logging.exception("Upload temp file missing before finalize")
        return (False, 500, UPLOAD_SAVE_FAILED)
    if actual_bytes != upload_bytes:
        _remove_staged_upload_temp(temp_path)
        return (False, 499, "Upload incomplete")

    os.makedirs(os.path.dirname(final_path_abs), exist_ok=True)
    try:
        shutil.move(temp_path, final_path_abs)
    except Exception:
        logging.exception("Upload save failed")
        return (False, 500, UPLOAD_SAVE_FAILED)

    if quota_service and is_feature_enabled("storage_quotas", False):
        quota_service.update_used_bytes(db_conn, username, upload_bytes)

    if audit_service:
        audit_service.log(
            db_conn,
            "file_upload",
            username=username,
            details=path_to_rel(final_path_abs, user_root),
            ip=remote_ip,
        )
    return (True, 200, UPLOAD_SUCCESSFUL)


def _remove_staged_upload_temp(temp_path: str | None) -> None:
    """Delete a staged upload temp file if it still exists."""
    if not temp_path:
        return
    try:
        if os.path.exists(temp_path):
            os.remove(temp_path)
    except OSError:
        logging.debug("staged upload temp remove failed", exc_info=True)


def _query_arg(query_args: dict, name: str) -> str | None:
    """First value for a Tornado query argument name."""
    if not query_args:
        return None
    raw_list = query_args.get(name)
    if raw_list is None:
        raw_list = query_args.get(name.encode("utf-8"))
    if not raw_list:
        return None
    raw = raw_list[0]
    if isinstance(raw, bytes):
        return raw.decode("utf-8", errors="replace")
    return str(raw)


# ---------------------------------------------------------------------------
# Helpers for bulk actions (reduce cognitive complexity)
# ---------------------------------------------------------------------------


def _bulk_delete_one(
    abspath, _path, db_conn, get_display_username, remote_ip, get_service, root_dir=None
):
    """Perform delete for one path. Return None on success, error message string on failure."""
    if os.path.isdir(abspath):
        if not is_feature_enabled("folder_delete", True):
            return FOLDER_DELETE_DISABLED_LOWER
        try:
            shutil.rmtree(abspath)
            get_service("audit_service").log(
                db_conn,
                "folder_delete",
                username=get_display_username(),
                details=path_to_rel(abspath, root_dir),
                ip=remote_ip,
            )
        except OSError as e:
            return str(e)
    else:
        if not is_feature_enabled("file_delete", True):
            return FILE_DELETE_DISABLED_LOWER
        try:
            os.remove(abspath)
            get_service("audit_service").log(
                db_conn,
                "file_delete",
                username=get_display_username(),
                details=path_to_rel(abspath, root_dir),
                ip=remote_ip,
            )
        except OSError as e:
            return str(e)
    return None


def _bulk_add_to_share_one(
    abspath, path, data, db_conn, get_display_username, remote_ip, get_service, root_dir=None
):
    """Add one path to share. Return None on success, error message string on failure."""
    share_id = data.get("share_id")
    if not share_id:
        return SHARE_ID_REQUIRED
    if len(str(share_id).strip()) > SHARE_ID_MAX_LEN:
        return "share_id too long"
    if not db_conn:
        return DATABASE_UNAVAILABLE
    share = get_service("share_service").get_share(db_conn, share_id)
    if not share:
        return SHARE_NOT_FOUND
    stype = share.get("share_type", "static")
    if stype == "tag":
        return "Cannot add paths to a tag-based share"
    if stype == "dynamic" and os.path.isfile(abspath):
        return (
            "Cannot add individual files to a dynamic share; add the folder root instead"
        )
    paths_list = list(share.get("paths") or [])
    rel = path_to_rel(abspath, root_dir)
    if rel in paths_list:
        return None
    paths_list.append(rel)
    if not get_service("share_service").update_share(
        db_conn, share_id, paths=paths_list
    ):
        return UPDATE_FAILED
    get_service("audit_service").log(
        db_conn,
        "share_update",
        username=get_display_username(),
        details=f"add path to {share_id}: {rel}",
        ip=remote_ip,
    )
    return None


class BulkActionCommand(Protocol):
    """Command contract for one bulk action execution."""

    def __call__(
        self,
        abspath: str,
        path: str,
        data: dict,
        db_conn: Any,
        get_display_username: Callable[[], str],
        remote_ip: str,
        get_service: Callable[[str], Any],
        root_dir: str | None,
    ) -> str | None: ...


class DeleteBulkActionCommand:
    def __call__(
        self,
        abspath: str,
        path: str,
        data: dict,
        db_conn: Any,
        get_display_username: Callable[[], str],
        remote_ip: str,
        get_service: Callable[[str], Any] = None,
        root_dir: str | None = None,
    ) -> str | None:
        return _bulk_delete_one(
            abspath, path, db_conn, get_display_username, remote_ip, get_service, root_dir
        )


class AddToShareBulkActionCommand:
    def __call__(
        self,
        abspath: str,
        path: str,
        data: dict,
        db_conn: Any,
        get_display_username: Callable[[], str],
        remote_ip: str,
        get_service: Callable[[str], Any] = None,
        root_dir: str | None = None,
    ) -> str | None:
        return _bulk_add_to_share_one(
            abspath, path, data, db_conn, get_display_username, remote_ip, get_service, root_dir
        )


_BULK_ACTION_COMMANDS: dict[str, BulkActionCommand] = {
    "delete": DeleteBulkActionCommand(),
    "add_to_share": AddToShareBulkActionCommand(),
}


def _process_bulk_action(
    action, abspath, path, data, db_conn, get_display_username, remote_ip, get_service, root_dir=None
):
    """Dispatch one bulk action. Return None on success, error string on failure."""
    command = _BULK_ACTION_COMMANDS.get(action)
    if command:
        return command(
            abspath, path, data, db_conn, get_display_username, remote_ip, get_service, root_dir
        )
    return UNSUPPORTED_ACTION


def _tune_request_socket(handler: BaseHandler, *, rcvbuf: int = 16 * 1024 * 1024) -> None:
    """Enlarge the accepted-connection receive buffer (listen-socket opts do not always inherit)."""
    try:
        stream = getattr(getattr(handler, "request", None), "connection", None)
        stream = getattr(stream, "stream", None) or stream
        sock = getattr(stream, "socket", None)
        if sock is None:
            return
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, rcvbuf)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, rcvbuf)
    except OSError:
        pass


def _upload_request_content_length(handler: BaseHandler) -> int:
    content_length = handler.request.headers.get("Content-Length")
    try:
        return max(0, int(content_length)) if content_length else 0
    except ValueError:
        return 0


def _upload_exceeds_direct_limit(strategy: dict, content_length: int) -> bool:
    return (
        strategy["uploadTransport"] != "stream"
        and content_length > int(strategy["directUploadMaxBytes"])
    )


def _parse_upload_headers(handler: BaseHandler) -> tuple[str, str]:
    raw_dir = (
        handler.request.headers.get("X-Upload-Dir")
        or _query_arg(handler.request.arguments, "upload_dir")
        or ""
    )
    raw_filename = (
        handler.request.headers.get("X-Upload-Filename")
        or _query_arg(handler.request.arguments, "upload_filename")
        or ""
    )
    return unquote(raw_dir), unquote(raw_filename)


def _init_upload_stream_state(handler: "UploadHandler") -> None:
    handler._reject = False
    handler._reject_status = 400
    handler._reject_reason = None
    handler._temp_path = None
    handler._aiofile = None
    handler._sync_file = None
    handler._fast_writer = None
    handler._writer_error = None
    handler._buffer = deque()
    handler._writer_task = None
    handler._writing = False
    handler._moved = False
    handler._bytes_received = 0
    handler._too_large = False
    handler._direct_limit_exceeded = False
    handler._expected_bytes = 0
    handler._client_closed = False
    handler._use_native_pump = False
    handler._native_pump = None
    handler._native_cancel = None
    handler._upload_file_fd = None
    handler._strategy = constants_module.get_effective_transfer_strategy()


async def _open_upload_staging_file(
    handler: "UploadHandler", final_path_abs: str, content_length: int
) -> None:
    user_root = get_user_root(handler)
    dest_dir = os.path.dirname(final_path_abs) or user_root
    os.makedirs(dest_dir, exist_ok=True)
    fd, handler._temp_path = tempfile.mkstemp(prefix=".aird_up_")
    handler._upload_file_fd = fd
    use_fast_path = (
        handler._strategy.get("uploadTransport") == "stream"
        or content_length >= (4 * 1024 * 1024)
    )
    if use_fast_path:
        if (
            handler._strategy.get("uploadTransport") == "stream"
            and content_length >= (4 * 1024 * 1024)
            and try_start_native_socket_upload(handler, fd, content_length)
        ):
            handler._use_native_pump = True
            handler._fast_writer = None
            handler._sync_file = None
            handler._aiofile = None
        else:
            handler._fast_writer = FastUploadWriter(fd)
            handler._upload_file_fd = None
            handler._sync_file = None
            handler._aiofile = None
    else:
        handler._fast_writer = None
        handler._sync_file = await asyncio.to_thread(
            os.fdopen, fd, "wb", 8 * 1024 * 1024
        )
        handler._upload_file_fd = None
        handler._aiofile = None


@tornado.web.stream_request_body
class UploadHandler(BaseHandler):
    """Single-request HTTP upload (WireGuard/LAN stream + small Open/CF files)."""

    def check_xsrf_cookie(self) -> None:
        """Streamed uploads send raw body; accept X-XSRFToken header or ?_xsrf= query param."""
        cookie_token = self.get_cookie("_xsrf")
        if not cookie_token:
            raise tornado.web.HTTPError(403, "'_xsrf' cookie missing")
        provided = self.request.headers.get("X-XSRFToken")
        if not provided:
            provided = _query_arg(self.request.arguments, "_xsrf")
        if not provided or not secrets.compare_digest(provided, cookie_token):
            raise tornado.web.HTTPError(403, "XSRF validation failed")

    async def prepare(self):
        BaseHandler.prepare(self)
        self.sync_upload_config_from_db()
        try:
            self.request.connection.set_max_body_size(
                constants_module.MAX_FILE_SIZE + (1024 * 1024)
            )
        except (AttributeError, RuntimeError):
            pass
        _tune_request_socket(self)
        self.check_xsrf_cookie()
        if not self.get_current_user():
            raise tornado.web.HTTPError(403, "Authentication required")
        if not self.has_modify_privileges():
            raise tornado.web.HTTPError(403, ACCESS_DENIED)

        _init_upload_stream_state(self)
        content_length_value = _upload_request_content_length(self)
        self._expected_bytes = content_length_value
        if _upload_exceeds_direct_limit(self._strategy, content_length_value):
            self._reject = True
            self._reject_status = 413
            self._reject_reason = (
                "Direct upload exceeds this hosting profile's limit; "
                "use a resumable ranged upload"
            )
            return

        if not is_feature_enabled("file_upload", True):
            self._reject = True
            self._reject_reason = FILE_UPLOAD_DISABLED
            return

        self.upload_dir, self.filename = _parse_upload_headers(self)
        if not self.filename:
            self._reject = True
            self._reject_reason = MISSING_UPLOAD_FILENAME_HEADER
            return

        user_root = get_user_root(self)
        final_path_abs, upload_err = _validate_upload_destination(
            self.upload_dir,
            self.filename,
            user_root,
            mounts_for_username(self.db_conn, get_username_string_for_db(self) or ""),
        )
        if upload_err is not None:
            self._reject = True
            self._reject_status, self._reject_reason = upload_err
            return

        await _open_upload_staging_file(self, final_path_abs, content_length_value)

    def _feed_upload_chunk(self, chunk: bytes) -> None:
        writer = getattr(self, "_fast_writer", None)
        if writer is not None:
            try:
                writer.feed(chunk)
            except Exception as exc:
                self._writer_error = exc
                logging.warning("Upload feed failed: %s", exc)
            return
        sync_file = getattr(self, "_sync_file", None)
        if sync_file is not None:
            try:
                sync_file.write(chunk)
            except OSError as exc:
                self._writer_error = exc
                logging.warning("Upload write failed: %s", exc)
            return
        self._buffer.append(chunk)
        if not self._writing:
            self._writing = True
            self._writer_task = asyncio.create_task(self._drain_buffer())

    def data_received(self, chunk: bytes) -> None:
        if self._reject or getattr(self, "_client_closed", False):
            return
        if getattr(self, "_use_native_pump", False):
            return
        self._bytes_received += len(chunk)
        if self._bytes_received > constants_module.MAX_FILE_SIZE:
            self._too_large = True
            return
        strategy = getattr(
            self,
            "_strategy",
            constants_module.get_effective_transfer_strategy(),
        )
        if _upload_exceeds_direct_limit(strategy, self._bytes_received):
            self._direct_limit_exceeded = True
            return
        self._feed_upload_chunk(chunk)

    async def _drain_buffer(self) -> None:
        try:
            while self._buffer:
                data = self._buffer.popleft()
                if self._aiofile is not None:
                    await self._aiofile.write(data)
        finally:
            self._writing = False
            if self._buffer and not self._reject:
                self._writing = True
                self._writer_task = asyncio.create_task(self._drain_buffer())

    async def _finalize_native_pump(self) -> bool:
        """Drain native pump if present. Return True when handled."""
        pump = getattr(self, "_native_pump", None)
        if pump is None:
            return False
        await asyncio.to_thread(pump.wait)
        self._bytes_received = pump.bytes_written
        if pump.error is not None:
            self._writer_error = pump.error
        self._native_pump = None
        upload_fd = getattr(self, "_upload_file_fd", None)
        if upload_fd is not None:
            try:
                os.close(upload_fd)
            except OSError:
                logging.debug("native upload fd close failed", exc_info=True)
            self._upload_file_fd = None
        return True

    async def _finalize_fast_writer(self) -> None:
        writer = getattr(self, "_fast_writer", None)
        if writer is None:
            return
        await asyncio.to_thread(writer.finish)
        if writer.error is not None:
            self._writer_error = writer.error
        self._fast_writer = None

    async def _await_upload_writer_tasks(self) -> None:
        while self._writer_task is not None:
            task = self._writer_task
            self._writer_task = None
            try:
                await task
            except Exception:
                logging.debug("upload writer task await failed", exc_info=True)

    async def _close_upload_files(self) -> None:
        sync_file = getattr(self, "_sync_file", None)
        if sync_file is not None:
            try:
                await asyncio.to_thread(sync_file.flush)
                await asyncio.to_thread(sync_file.close)
            except Exception:
                logging.debug("upload sync file close failed", exc_info=True)
            self._sync_file = None
        if self._aiofile is not None:
            try:
                await self._aiofile.flush()
                await self._aiofile.close()
            except Exception:
                logging.debug("upload aiofile close failed", exc_info=True)
            self._aiofile = None

    async def _finalize_stream(self):
        if await self._finalize_native_pump():
            return
        await self._finalize_fast_writer()
        await self._await_upload_writer_tasks()
        await self._close_upload_files()

    def _respond_upload_stream_error(self) -> bool:
        """Return True when an error response was written."""
        if getattr(self, "_client_closed", False):
            return True
        if getattr(self, "_writer_error", None) is not None:
            self.set_status(500)
            self.write(UPLOAD_SAVE_FAILED)
            return True
        if getattr(self, "_direct_limit_exceeded", False):
            self.set_status(413)
            self.write(
                "Direct upload exceeds this hosting profile's limit; "
                "use a resumable ranged upload"
            )
            return True
        if self._too_large:
            limit_mb = constants_module.UPLOAD_CONFIG.get("max_file_size_mb", 512)
            self.set_status(413)
            self.write(FILE_TOO_LARGE_TEMPLATE.format(limit_mb=limit_mb))
            return True
        expected = getattr(self, "_expected_bytes", 0) or 0
        if expected > 0 and self._bytes_received != expected:
            _remove_staged_upload_temp(self._temp_path)
            self.set_status(499)
            self.write("Upload incomplete")
            return True
        return False

    @tornado.web.authenticated
    @require_action("file.write")
    @require_modify_access()
    async def post(self):
        if not self.require_feature(
            "file_upload", True, body=FILE_UPLOAD_DISABLED_ADMIN
        ):
            return

        if self._reject:
            self.set_status(getattr(self, "_reject_status", 400))
            self.write(self._reject_reason or BAD_REQUEST)
            return

        await self._finalize_stream()

        if self._respond_upload_stream_error():
            return

        upload_bytes = self._bytes_received
        success, status, message = await asyncio.to_thread(
            finalize_upload_to_disk,
            upload_dir=self.upload_dir,
            filename=self.filename,
            temp_path=self._temp_path,
            user_root=get_user_root(self),
            username=self.get_display_username(),
            db_conn=self.db_conn,
            quota_service=self.get_service("quota_service"),
            audit_service=self.get_service("audit_service"),
            remote_ip=self.request.remote_ip,
            upload_bytes=upload_bytes,
        )
        if success:
            self._moved = True
        self.set_status(status)
        self.write(message)

    def _abort_upload_staging(self) -> None:
        cancel = getattr(self, "_native_cancel", None)
        if cancel is not None:
            try:
                cancel.cancel()
            except Exception:
                logging.debug("native upload cancel failed", exc_info=True)
        pump = getattr(self, "_native_pump", None)
        if pump is not None:
            pump.abort()
            self._native_pump = None
        writer = getattr(self, "_fast_writer", None)
        if writer is not None:
            writer.abort()
            self._fast_writer = None
        sync_file = getattr(self, "_sync_file", None)
        if sync_file is not None:
            try:
                sync_file.close()
            except Exception:
                logging.debug("upload sync file close failed", exc_info=True)
            self._sync_file = None
        if getattr(self, "_temp_path", None) and not getattr(self, "_moved", False):
            _remove_staged_upload_temp(self._temp_path)
        upload_fd = getattr(self, "_upload_file_fd", None)
        if upload_fd is not None:
            try:
                os.close(upload_fd)
            except OSError:
                logging.debug("upload file fd close on abort failed", exc_info=True)
            self._upload_file_fd = None

    def on_connection_close(self) -> None:
        self._client_closed = True
        self._abort_upload_staging()

    def on_finish(self) -> None:
        self._abort_upload_staging()


class CreateFolderHandler(BaseHandler):
    @tornado.web.authenticated
    @require_action("file.write")
    @require_modify_access()
    def post(self):
        if not self.require_feature("folder_create", True, body=FOLDER_CREATE_DISABLED):
            return
        parent = self.get_argument("parent", "").strip().strip("/")
        name = self.get_argument("name", "").strip()
        if not name or name in (".", "..") or "/" in name or "\\" in name:
            self.set_status(400)
            self.write(INVALID_FOLDER_NAME)
            return
        if len(name) > 255:
            self.set_status(400)
            self.write(FOLDER_NAME_TOO_LONG)
            return
        if _deny_write_rel(self, parent):
            return
        parent_abs, confine = resolve_handler_rel(self, parent)
        if not parent_abs or not confine:
            self.set_status(403)
            self.write(ACCESS_DENIED)
            return
        new_dir_abs = os.path.abspath(os.path.join(parent_abs, name))
        if not is_within_root(parent_abs, confine) or not is_within_root(
            new_dir_abs, confine
        ):
            self.set_status(403)
            self.write(ACCESS_DENIED)
            return
        if os.path.exists(new_dir_abs):
            self.set_status(409)
            self.write(CONFLICT_EXISTS)
            return
        try:
            os.makedirs(new_dir_abs, exist_ok=False)
        except OSError:
            logging.exception("CreateFolder error")
            self.set_status(500)
            self.write(FOLDER_CREATE_FAILED)
            return
        username = (
            self.get_display_username()
            if hasattr(self, "get_display_username")
            else None
        )
        self.get_service("audit_service").log(
            self.db_conn,
            "folder_create",
            username=username,
            details=path_to_rel(new_dir_abs, get_user_root(self)),
            ip=self.request.remote_ip,
        )
        if self.request.headers.get("Accept") == HEADER_APPLICATION_JSON:
            self.set_header("Content-Type", HEADER_APPLICATION_JSON)
            self.write({"ok": True, "path": (parent + "/" + name) if parent else name})
            return
        self.redirect(
            FILES_URL_STRING + ((parent + "/" + name) if parent else name) + "/"
        )


def path_to_rel(abspath, root_dir=None):
    """Return *abspath* relative to *root_dir* (required in multi-user mode)."""
    try:
        if root_dir is None:
            import aird.constants as _c

            root_dir = _c.ROOT_DIR
        return os.path.relpath(abspath, root_dir).replace("\\", "/")
    except Exception:
        return abspath


class DeleteHandler(BaseHandler):
    def _delete_directory(self, abspath):
        """Delete a directory. Returns True if handled (caller should return early on failure)."""
        if not self.require_feature("folder_delete", True, body=FOLDER_DELETE_DISABLED):
            return False
        recursive = self.get_argument("recursive", "0") == "1"
        if not recursive and os.listdir(abspath):
            self.set_status(400)
            self.write(FOLDER_NOT_EMPTY)
            return False
        shutil.rmtree(abspath)
        self.get_service("audit_service").log(
            self.db_conn,
            "folder_delete",
            username=self.get_display_username(),
            details=path_to_rel(abspath, get_user_root(self)),
            ip=self.request.remote_ip,
        )
        return True

    def _delete_file(self, abspath):
        """Delete a file. Returns True if handled (caller should return early on failure)."""
        if not self.require_feature("file_delete", True, body=FILE_DELETE_DISABLED):
            return False
        file_size = get_file_size_safe(abspath)
        os.remove(abspath)
        if is_feature_enabled("storage_quotas", False) and file_size > 0:
            self.get_service("quota_service").update_used_bytes(
                self.db_conn, self.get_display_username(), -file_size
            )
        self.get_service("audit_service").log(
            self.db_conn,
            "file_delete",
            username=self.get_display_username(),
            details=path_to_rel(abspath, get_user_root(self)),
            ip=self.request.remote_ip,
        )
        return True

    @tornado.web.authenticated
    @require_action("file.delete")
    @require_modify_access()
    def post(self):
        path = self.get_argument("path", "")
        if _deny_write_rel(self, path, as_target=True):
            return
        abspath, confine = resolve_handler_rel(self, path)
        if not abspath or not confine or not is_within_root(abspath, confine):
            self.set_status(403)
            self.write(ACCESS_DENIED)
            return
        if os.path.isdir(abspath):
            if not self._delete_directory(abspath):
                return
        elif os.path.isfile(abspath):
            if not self._delete_file(abspath):
                return
        else:
            self.set_status(404)
            self.write(FILE_OR_FOLDER_NOT_FOUND)
            return
        parent = os.path.dirname(path)
        if self.request.headers.get("Accept") == HEADER_APPLICATION_JSON:
            self.set_header("Content-Type", HEADER_APPLICATION_JSON)
            self.write({"ok": True})
            return
        self.redirect(FILES_URL_STRING + parent if parent else FILES_URL_STRING)


class RenameHandler(BaseHandler):
    @tornado.web.authenticated
    @require_action("file.rename")
    @require_modify_access()
    def post(self):
        if not self.require_feature("file_rename", True, body=FILE_RENAME_DISABLED):
            return

        path = self.get_argument("path", "").strip()
        new_name = self.get_argument("new_name", "").strip()

        # Input validation
        if not path or not new_name:
            self.set_status(400)
            self.write(INVALID_REQUEST_PATH_AND_NAME)
            return

        # Validate new filename
        if new_name in [".", ".."] or "/" in new_name or "\\" in new_name:
            self.set_status(400)
            self.write(INVALID_FILENAME)
            return

        if len(new_name) > 255:
            self.set_status(400)
            self.write(FILENAME_TOO_LONG)
            return

        if _deny_write_rel(self, path, as_target=True):
            return
        if _deny_write_rel(self, os.path.dirname(path.replace("\\", "/"))):
            return

        abspath, confine = resolve_handler_rel(self, path)
        parent_rel = os.path.dirname(path.replace("\\", "/")).replace("\\", "/")
        new_rel = f"{parent_rel}/{new_name}" if parent_rel else new_name
        new_abspath, new_confine = resolve_handler_rel(self, new_rel)
        if (
            not abspath
            or not confine
            or not new_abspath
            or not new_confine
            or not is_within_root(abspath, confine)
            or not is_within_root(new_abspath, new_confine)
        ):
            self.set_status(403)
            self.write(ACCESS_DENIED)
            return

        if not os.path.exists(abspath):
            self.set_status(404)
            self.write(FILE_NOT_FOUND)
            return

        try:
            os.rename(abspath, new_abspath)
        except OSError:
            self.set_status(500)
            self.write(RENAME_FAILED)
            return

        self.get_service("audit_service").log(
            self.db_conn,
            "rename",
            username=self.get_display_username(),
            details=f"{path} -> {new_name}",
            ip=self.request.remote_ip,
        )
        parent = os.path.dirname(path)
        if self.request.headers.get("Accept") == HEADER_APPLICATION_JSON:
            self.set_header("Content-Type", HEADER_APPLICATION_JSON)
            self.write({"ok": True})
            return
        self.redirect(FILES_URL_STRING + parent if parent else FILES_URL_STRING)


def _resolve_copy_move_paths(handler, path: str, dest: str) -> tuple[str, str] | None:
    """Validate copy/move path pair; write error response and return None on failure."""
    if not path or not dest:
        handler.set_status(400)
        handler.write(INVALID_REQUEST_PATH_DEST)
        return None
    if _deny_write_rel(handler, path, as_target=True) or _deny_write_rel(handler, dest):
        return None
    src_abs, src_confine = resolve_handler_rel(handler, path)
    dest_abs, dest_confine = resolve_handler_rel(handler, dest)
    if (
        not src_abs
        or not src_confine
        or not dest_abs
        or not dest_confine
        or not is_within_root(src_abs, src_confine)
        or not is_within_root(dest_abs, dest_confine)
    ):
        handler.set_status(403)
        handler.write(ACCESS_DENIED_SHORT)
        return None
    if not os.path.exists(src_abs):
        handler.set_status(404)
        handler.write(SOURCE_NOT_FOUND)
        return None
    if os.path.exists(dest_abs):
        handler.set_status(409)
        handler.write(DESTINATION_EXISTS)
        return None
    return src_abs, dest_abs


class CopyHandler(BaseHandler):
    @tornado.web.authenticated
    @require_action("file.write")
    @require_modify_access()
    def post(self):
        if not self.require_feature("file_rename", True, body=COPY_DISABLED):
            return
        path = self.get_argument("path", "").strip()
        dest = self.get_argument("dest", "").strip()
        resolved = _resolve_copy_move_paths(self, path, dest)
        if resolved is None:
            return
        src_abs, dest_abs = resolved
        try:
            if os.path.isdir(src_abs):
                shutil.copytree(src_abs, dest_abs)
            else:
                shutil.copy2(src_abs, dest_abs)
        except OSError:
            logging.exception("Copy error")
            self.set_status(500)
            self.write(COPY_FAILED)
            return
        self.get_service("audit_service").log(
            self.db_conn,
            "copy",
            username=self.get_display_username(),
            details=f"{path} -> {dest}",
            ip=self.request.remote_ip,
        )
        if self.request.headers.get("Accept") == HEADER_APPLICATION_JSON:
            self.set_header("Content-Type", HEADER_APPLICATION_JSON)
            self.write({"ok": True})
            return
        self.redirect(
            FILES_URL_STRING + os.path.dirname(dest)
            if os.path.dirname(dest)
            else FILES_URL_STRING
        )


class MoveHandler(BaseHandler):
    @tornado.web.authenticated
    @require_action("file.write")
    @require_modify_access()
    def post(self):
        if not self.require_feature("file_rename", True, body=MOVE_DISABLED):
            return
        path = self.get_argument("path", "").strip()
        dest = self.get_argument("dest", "").strip()
        resolved = _resolve_copy_move_paths(self, path, dest)
        if resolved is None:
            return
        src_abs, dest_abs = resolved
        try:
            shutil.move(src_abs, dest_abs)
        except OSError:
            logging.exception("Move error")
            self.set_status(500)
            self.write(MOVE_FAILED)
            return
        self.get_service("audit_service").log(
            self.db_conn,
            "move",
            username=self.get_display_username(),
            details=f"{path} -> {dest}",
            ip=self.request.remote_ip,
        )
        if self.request.headers.get("Accept") == HEADER_APPLICATION_JSON:
            self.set_header("Content-Type", HEADER_APPLICATION_JSON)
            self.write({"ok": True})
            return
        self.redirect(
            FILES_URL_STRING + os.path.dirname(dest)
            if os.path.dirname(dest)
            else FILES_URL_STRING
        )


class DownloadZipHandler(XSRFTokenMixin, BaseHandler):
    """Zip multiple files/folders into one download (temp archive, deleted after send)."""

    def initialize(self):
        super().initialize()
        self._zip_temp_path: str | None = None

    def on_finish(self) -> None:
        path = self._zip_temp_path
        self._zip_temp_path = None
        if path and os.path.isfile(path):
            try:
                os.remove(path)
            except OSError:
                logging.debug("zip temp remove failed in on_finish", exc_info=True)
        super().on_finish()

    @tornado.web.authenticated
    @require_action("file.read")
    async def post(self):
        if not self.require_feature(
            "file_download", True, body="File download is disabled"
        ):
            return
        if len(self.request.body) > MAX_BULK_JSON_BYTES:
            self.set_status(400)
            self.write(BAD_REQUEST)
            return
        try:
            data = json.loads(
                self.request.body.decode("utf-8", errors="replace") or "{}"
            )
        except Exception:
            self.set_status(400)
            self.write(INVALID_JSON)
            return
        paths = data.get("paths")
        if not isinstance(paths, list) or not paths:
            self.set_status(400)
            self.write(PATHS_REQUIRED)
            return
        if len(paths) > MAX_BULK_PATHS:
            self.set_status(400)
            self.write(BAD_REQUEST)
            return

        root = get_user_root(self)
        mounts = mounts_for_username(self.db_conn, get_username_string_for_db(self) or "")
        try:
            entries = await asyncio.to_thread(collect_zip_entries, root, paths, mounts)
            self._zip_temp_path = await asyncio.to_thread(build_zip_file, entries)
            filename = (data.get("filename") or "aird-download.zip").strip()
            if not filename.lower().endswith(".zip"):
                filename += ".zip"
            self.set_header("Content-Type", "application/zip")
            self.set_header(
                "Content-Disposition", f'attachment; filename="{filename}"'
            )
            async for chunk in MMapFileHandler.serve_file_chunk(self._zip_temp_path):
                self.write(chunk)
                await self.flush()
        except ZipDownloadError as exc:
            self.set_status(exc.status)
            self.write(str(exc))
        except Exception:
            logging.exception("Download zip failed")
            self.set_status(500)
            self.write("Failed to create zip archive")


def _validate_bulk_path(path, root: str, mounts=None) -> tuple[str | None, str | None]:
    """Return (abspath, error) — error is non-None if path is invalid."""
    from aird.core.browse_paths import resolve_rel

    if not isinstance(path, str):
        return None, INVALID_PATH
    path = path.strip().strip("/")
    abspath, confine = resolve_rel(root, path, mounts or [])
    if not abspath or not confine or not is_within_root(abspath, confine):
        return None, ACCESS_DENIED_LOWER
    if not os.path.exists(abspath):
        return None, NOT_FOUND_LOWER
    return abspath, None


def _run_bulk_path_action(
    handler: BaseHandler,
    action: str,
    path,
    data: dict,
    root: str,
    mounts,
    remote_ip: str,
) -> dict:
    abspath, path_err = _validate_bulk_path(path, root, mounts)
    display_path = path.strip().strip("/") if isinstance(path, str) else path
    if path_err:
        return {"path": display_path, "ok": False, "error": path_err}
    err = _process_bulk_action(
        action,
        abspath,
        display_path,
        data,
        handler.db_conn,
        handler.get_display_username,
        remote_ip,
        handler.get_service,
        root,
    )
    if err:
        return {"path": display_path, "ok": False, "error": err}
    return {"path": display_path, "ok": True}


class BulkHandler(BaseHandler):
    @tornado.web.authenticated
    @require_action("file.write")
    @require_modify_access()
    def post(self):
        if len(self.request.body) > MAX_BULK_JSON_BYTES:
            self.set_status(400)
            self.write(BAD_REQUEST)
            return
        try:
            data = json.loads(
                self.request.body.decode("utf-8", errors="replace") or "{}"
            )
        except Exception:
            self.set_status(400)
            self.write(INVALID_JSON)
            return
        action = (data.get("action") or "").strip().lower()
        paths = data.get("paths")
        if not isinstance(paths, list) or not paths:
            self.set_status(400)
            self.write(PATHS_REQUIRED)
            return
        if len(paths) > MAX_BULK_PATHS:
            self.set_status(400)
            self.write(BAD_REQUEST)
            return
        root = get_user_root(self)
        mounts = mounts_for_username(self.db_conn, get_username_string_for_db(self) or "")
        remote_ip = self.request.remote_ip
        results = {"ok": True, "results": []}
        for path in paths:
            item = _run_bulk_path_action(self, action, path, data, root, mounts, remote_ip)
            if not item["ok"]:
                results["ok"] = False
            results["results"].append(item)
        self.set_header("Content-Type", HEADER_APPLICATION_JSON)
        self.write(results)


class EditHandler(BaseHandler):
    def _parse_edit_body(self) -> tuple[str, str] | None:
        """Return (path, content) or None on parse error (writes 400 response)."""
        content_type = self.request.headers.get("Content-Type", "")
        if content_type.startswith(HEADER_APPLICATION_JSON):
            try:
                data = json.loads(self.request.body.decode("utf-8", errors="replace") or "{}")
                return data.get("path", ""), data.get("content", "")
            except Exception:
                self.set_status(400)
                self.write(INVALID_JSON_REQUEST)
                return None
        return self.get_argument("path", ""), self.get_argument("content", "")

    def _atomic_write(self, abspath, content: str) -> None:
        directory_name = os.path.dirname(abspath)
        os.makedirs(directory_name, exist_ok=True)
        temp_fd = tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=directory_name, delete=False)
        temp_path = temp_fd.name
        try:
            temp_fd.write(content)
            temp_fd.close()
            os.replace(temp_path, abspath)
        except Exception:
            try:
                os.unlink(temp_path)
            except OSError:
                pass
            raise

    @tornado.web.authenticated
    @require_action("file.write")
    @require_modify_access()
    def post(self):
        if not self.require_feature("file_edit", True, body=FILE_EDIT_DISABLED):
            return

        content_type = self.request.headers.get("Content-Type", "")
        if len(self.request.body) > EDIT_JSON_BODY_MAX_BYTES:
            self.set_status(413)
            self.write(INVALID_JSON_REQUEST if content_type.startswith(HEADER_APPLICATION_JSON) else BAD_REQUEST)
            return

        parsed = self._parse_edit_body()
        if parsed is None:
            return
        path, content = parsed

        if len(str(path).strip()) > REL_PATH_MAX_LEN:
            self.set_status(400)
            self.write(INVALID_PATH)
            return

        safe_path = str(path).lstrip("/\\")
        if _deny_write_rel(self, safe_path):
            return
        abspath, confine = resolve_handler_rel(self, safe_path)
        if not abspath or not confine or not is_within_root(str(abspath), confine):
            logging.warning(f"EditHandler: access denied for path {path}.")
            self.set_status(403)
            self.write(ACCESS_DENIED_WITH_PERIOD)
            return

        if not os.path.isfile(abspath):
            logging.warning(f"EditHandler: file not found at path {path}.")
            self.set_status(404)
            self.write(FILE_NOT_FOUND)
            return

        try:
            self._atomic_write(abspath, content)
            self.get_service("audit_service").log(
                self.db_conn, "file_edit",
                username=self.get_display_username(),
                details=path_to_rel(str(abspath), confine),
                ip=self.request.remote_ip,
            )
            self.set_status(200)
            if self.request.headers.get("Accept") == HEADER_APPLICATION_JSON:
                self.write({"ok": True})
            else:
                self.write(FILE_SAVED_SUCCESSFULLY)
        except Exception:
            logging.exception("File save error")
            self.set_status(500)
            self.write(FILE_SAVE_ERROR)


class CloudUploadHandler(BaseHandler):
    @tornado.web.authenticated
    @require_action("file.write")
    @require_modify_access()
    async def post(self, provider_name: str):
        self.sync_upload_config_from_db()
        manager: CloudManager = self.application.settings.get(
            "cloud_manager", CLOUD_MANAGER
        )
        provider = manager.get(provider_name)
        if not provider:
            self.set_status(404)
            self.write({"error": PROVIDER_NOT_CONFIGURED})
            return

        uploads = self.request.files.get("file")
        if not uploads:
            self.set_status(400)
            self.write({"error": NO_FILE_UPLOADED})
            return

        upload = uploads[0]
        body: bytes = upload.get("body", b"")
        raw_filename = upload.get("filename") or "upload.bin"
        filename = sanitize_cloud_filename(raw_filename)
        content_type = upload.get("content_type") or None

        size = len(body)
        if size == 0:
            # Allow empty files but still enforce limit check below
            pass
        if size > constants_module.MAX_FILE_SIZE:
            self.set_status(413)
            self.write({"error": FILE_TOO_LARGE})
            return

        parent_id = self.get_body_argument("parent_id", None, strip=True)
        parent_id = parent_id or None

        def _do_upload():
            stream = BytesIO(body)
            return provider.upload_file(
                stream,
                name=filename,
                parent_id=parent_id,
                size=size,
                content_type=content_type,
            )

        try:
            cloud_file = await asyncio.to_thread(_do_upload)
        except Exception as exc:
            self.handle_cloud_error(
                exc,
                f"Failed to upload file to cloud provider {provider_name}",
                CLOUD_UPLOAD_FAILED,
            )
            return

        self.write({"file": cloud_file.to_dict()})
