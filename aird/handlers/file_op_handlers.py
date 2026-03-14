import tornado.web
import os
import shutil
import tempfile
import json
import logging
import pathlib
from collections import deque
import asyncio
import aiofiles

from aird.handlers.base_handler import BaseHandler
from aird.utils.util import sanitize_cloud_filename, is_feature_enabled
from aird.core.security import (  # noqa: F401
    is_within_root,
    is_valid_websocket_origin,
    join_path,
)
from aird.db import log_audit
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
    ROOT_DIR,
    ALLOWED_UPLOAD_EXTENSIONS,
    CLOUD_MANAGER,
)
from aird.db import get_share_by_id, update_share
from aird.cloud import CloudManager, CloudProviderError
from io import BytesIO

HEADER_APPLICATION_JSON = "application/json"
FILES_URL_STRING = "/files/"


# ---------------------------------------------------------------------------
# Helpers for upload validation (reduce cognitive complexity)
# ---------------------------------------------------------------------------


def _validate_upload_destination(upload_dir, filename):
    """Validate upload dir and filename. Return (final_path_abs, None) or (None, (status, message))."""
    safe_dir_abs = os.path.realpath(
        os.path.join(ROOT_DIR, (upload_dir or "").strip().strip("/"))
    )
    if not is_within_root(safe_dir_abs, ROOT_DIR):
        return (None, (403, ACCESS_DENIED_PATH))
    safe_filename = os.path.basename(filename)
    if not safe_filename or safe_filename in (".", ".."):
        return (None, (400, INVALID_FILENAME))
    allow_all = constants_module.UPLOAD_CONFIG.get("allow_all_file_types", 0)
    if not allow_all:
        file_ext = os.path.splitext(safe_filename)[1].lower()
        allowed_set = (
            getattr(constants_module, "UPLOAD_ALLOWED_EXTENSIONS", None)
            or ALLOWED_UPLOAD_EXTENSIONS
        )
        if file_ext not in allowed_set:
            return (None, (415, UNSUPPORTED_FILE_TYPE))
    if len(safe_filename) > 255:
        return (None, (400, FILENAME_TOO_LONG))
    final_path_abs = os.path.realpath(os.path.join(safe_dir_abs, safe_filename))
    if not is_within_root(final_path_abs, safe_dir_abs):
        return (None, (403, ACCESS_DENIED_PATH))
    return (final_path_abs, None)


# ---------------------------------------------------------------------------
# Helpers for bulk actions (reduce cognitive complexity)
# ---------------------------------------------------------------------------


def _bulk_delete_one(abspath, _path, db_conn, get_display_username, remote_ip):
    """Perform delete for one path. Return None on success, error message string on failure."""
    if os.path.isdir(abspath):
        if not is_feature_enabled("folder_delete", True):
            return FOLDER_DELETE_DISABLED_LOWER
        try:
            shutil.rmtree(abspath)
            log_audit(
                db_conn,
                "folder_delete",
                username=get_display_username(),
                details=path_to_rel(abspath),
                ip=remote_ip,
            )
        except OSError as e:
            return str(e)
    else:
        if not is_feature_enabled("file_delete", True):
            return FILE_DELETE_DISABLED_LOWER
        try:
            os.remove(abspath)
            log_audit(
                db_conn,
                "file_delete",
                username=get_display_username(),
                details=path_to_rel(abspath),
                ip=remote_ip,
            )
        except OSError as e:
            return str(e)
    return None


def _bulk_add_to_share_one(
    abspath, path, data, db_conn, get_display_username, remote_ip
):
    """Add one path to share. Return None on success, error message string on failure."""
    share_id = data.get("share_id")
    if not share_id:
        return SHARE_ID_REQUIRED
    if not db_conn:
        return DATABASE_UNAVAILABLE
    share = get_share_by_id(db_conn, share_id)
    if not share:
        return SHARE_NOT_FOUND
    paths_list = list(share.get("paths") or [])
    rel = path_to_rel(abspath)
    if rel in paths_list:
        return None
    paths_list.append(rel)
    if not update_share(db_conn, share_id, paths=paths_list):
        return UPDATE_FAILED
    log_audit(
        db_conn,
        "share_update",
        username=get_display_username(),
        details=f"add path to {share_id}: {rel}",
        ip=remote_ip,
    )
    return None


def _process_bulk_action(
    action, abspath, path, data, db_conn, get_display_username, remote_ip
):
    """Dispatch one bulk action. Return None on success, error string on failure."""
    if action == "delete":
        return _bulk_delete_one(abspath, path, db_conn, get_display_username, remote_ip)
    if action == "add_to_share":
        return _bulk_add_to_share_one(
            abspath, path, data, db_conn, get_display_username, remote_ip
        )
    return UNSUPPORTED_ACTION


@tornado.web.stream_request_body
class UploadHandler(BaseHandler):
    async def prepare(self):
        # Defaults for safety
        self._reject: bool = False
        self._reject_reason: str | None = None
        self._temp_path: str | None = None
        self._aiofile = None
        self._buffer = deque()
        self._writer_task = None
        self._writing: bool = False
        self._moved: bool = False
        self._bytes_received: int = 0
        self._too_large: bool = False

        # Feature flag check (using SQLite-backed flags)
        # Deferred to post() for clear response, but avoid heavy work if disabled
        if not is_feature_enabled("file_upload", True):
            self._reject = True
            self._reject_reason = FILE_UPLOAD_DISABLED
            return

        # Read and decode headers provided by client
        self.upload_dir = self.request.headers.get("X-Upload-Dir", "")
        self.filename = self.request.headers.get("X-Upload-Filename", "")

        # Basic validation
        if not self.filename:
            self._reject = True
            self._reject_reason = MISSING_UPLOAD_FILENAME_HEADER
            return

        # Create temporary file for streamed writes
        fd, self._temp_path = tempfile.mkstemp(prefix="aird_upload_")
        # Close the low-level fd; we'll use aiofiles on the path
        os.close(fd)
        self._aiofile = await aiofiles.open(self._temp_path, "wb")

    def data_received(self, chunk: bytes) -> None:
        if self._reject:
            return
        # Track size to enforce limit at the end
        self._bytes_received += len(chunk)
        if self._bytes_received > constants_module.MAX_FILE_SIZE:
            self._too_large = True
            # We still accept the stream but won't persist it
            return

        # Queue the chunk and ensure a writer task is draining
        self._buffer.append(chunk)
        if not self._writing:
            self._writing = True
            self._writer_task = asyncio.create_task(self._drain_buffer())

    async def _drain_buffer(self) -> None:
        try:
            while self._buffer:
                data = self._buffer.popleft()
                await self._aiofile.write(data)
            await self._aiofile.flush()
        finally:
            self._writing = False

    @tornado.web.authenticated
    async def post(self):
        if not self.require_feature("file_upload", True, body=FILE_UPLOAD_DISABLED_ADMIN):
            return

        # If we rejected in prepare (bad/missing headers), report
        if self._reject:
            self.set_status(400)
            self.write(self._reject_reason or BAD_REQUEST)
            return

        # Wait for any in-flight writes to complete
        if self._writer_task is not None:
            try:
                await self._writer_task
            except Exception:
                pass

        # Close file to flush buffers
        if self._aiofile is not None:
            try:
                await self._aiofile.close()
            except Exception:
                pass

        # Enforce size limit
        if self._too_large:
            limit_mb = constants_module.UPLOAD_CONFIG.get("max_file_size_mb", 512)
            self.set_status(413)
            self.write(FILE_TOO_LARGE_TEMPLATE.format(limit_mb=limit_mb))
            return

        final_path_abs, upload_err = _validate_upload_destination(
            self.upload_dir, self.filename
        )
        if upload_err is not None:
            self.set_status(upload_err[0])
            self.write(upload_err[1])
            return

        os.makedirs(os.path.dirname(final_path_abs), exist_ok=True)

        try:
            shutil.move(self._temp_path, final_path_abs)
            self._moved = True
        except Exception as e:
            logging.error("Upload save failed: %s", e)
            self.set_status(500)
            self.write(UPLOAD_SAVE_FAILED)
            return

        log_audit(
            self.db_conn,
            "file_upload",
            username=self.get_display_username(),
            details=path_to_rel(final_path_abs),
            ip=self.request.remote_ip,
        )
        self.set_status(200)
        self.write(UPLOAD_SUCCESSFUL)

    def on_finish(self) -> None:
        # Clean up temp file on failures
        try:
            if getattr(self, "_temp_path", None) and not getattr(self, "_moved", False):
                if os.path.exists(self._temp_path):
                    try:
                        os.remove(self._temp_path)
                    except Exception:
                        pass
        except Exception:
            pass


class CreateFolderHandler(BaseHandler):
    @tornado.web.authenticated
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
        parent_abs = (
            os.path.abspath(os.path.join(ROOT_DIR, parent)) if parent else ROOT_DIR
        )
        new_dir_abs = os.path.abspath(os.path.join(parent_abs, name))
        if not is_within_root(parent_abs, ROOT_DIR) or not is_within_root(
            new_dir_abs, ROOT_DIR
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
        except OSError as e:
            logging.error("CreateFolder error: %s", e)
            self.set_status(500)
            self.write(FOLDER_CREATE_FAILED)
            return
        username = (
            self.get_display_username()
            if hasattr(self, "get_display_username")
            else None
        )
        log_audit(
            self.db_conn,
            "folder_create",
            username=username,
            details=path_to_rel(new_dir_abs),
            ip=self.request.remote_ip,
        )
        if self.request.headers.get("Accept") == HEADER_APPLICATION_JSON:
            self.set_header("Content-Type", HEADER_APPLICATION_JSON)
            self.write({"ok": True, "path": (parent + "/" + name) if parent else name})
            return
        self.redirect(
            FILES_URL_STRING + ((parent + "/" + name) if parent else name) + "/"
        )


def path_to_rel(abspath):
    try:
        return os.path.relpath(abspath, ROOT_DIR).replace("\\", "/")
    except Exception:
        return abspath


class DeleteHandler(BaseHandler):
    @tornado.web.authenticated
    def post(self):
        path = self.get_argument("path", "")
        abspath = os.path.abspath(os.path.join(ROOT_DIR, path))
        root = ROOT_DIR
        if not is_within_root(abspath, root):
            self.set_status(403)
            self.write(ACCESS_DENIED)
            return
        if os.path.isdir(abspath):
            if not self.require_feature("folder_delete", True, body=FOLDER_DELETE_DISABLED):
                return
            recursive = self.get_argument("recursive", "0") == "1"
            if not recursive and os.listdir(abspath):
                self.set_status(400)
                self.write(FOLDER_NOT_EMPTY)
                return
            shutil.rmtree(abspath)
            log_audit(
                self.db_conn,
                "folder_delete",
                username=self.get_display_username(),
                details=path_to_rel(abspath),
                ip=self.request.remote_ip,
            )
        elif os.path.isfile(abspath):
            if not self.require_feature("file_delete", True, body=FILE_DELETE_DISABLED):
                return
            os.remove(abspath)
            log_audit(
                self.db_conn,
                "file_delete",
                username=self.get_display_username(),
                details=path_to_rel(abspath),
                ip=self.request.remote_ip,
            )
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

        abspath = os.path.abspath(os.path.join(ROOT_DIR, path))
        new_abspath = os.path.abspath(
            os.path.join(ROOT_DIR, os.path.dirname(path), new_name)
        )
        root = ROOT_DIR
        if not (is_within_root(abspath, root) and is_within_root(new_abspath, root)):
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

        log_audit(
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


class CopyHandler(BaseHandler):
    @tornado.web.authenticated
    def post(self):
        if not self.require_feature("file_rename", True, body=COPY_DISABLED):
            return
        path = self.get_argument("path", "").strip()
        dest = self.get_argument("dest", "").strip()
        if not path or not dest:
            self.set_status(400)
            self.write(INVALID_REQUEST_PATH_DEST)
            return
        src_abs = os.path.abspath(os.path.join(ROOT_DIR, path))
        dest_abs = os.path.abspath(os.path.join(ROOT_DIR, dest))
        if not is_within_root(src_abs, ROOT_DIR) or not is_within_root(
            dest_abs, ROOT_DIR
        ):
            self.set_status(403)
            self.write(ACCESS_DENIED_SHORT)
            return
        if not os.path.exists(src_abs):
            self.set_status(404)
            self.write(SOURCE_NOT_FOUND)
            return
        if os.path.exists(dest_abs):
            self.set_status(409)
            self.write(DESTINATION_EXISTS)
            return
        try:
            if os.path.isdir(src_abs):
                shutil.copytree(src_abs, dest_abs)
            else:
                shutil.copy2(src_abs, dest_abs)
        except OSError as e:
            logging.error("Copy error: %s", e)
            self.set_status(500)
            self.write(COPY_FAILED)
            return
        log_audit(
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
    def post(self):
        if not self.require_feature("file_rename", True, body=MOVE_DISABLED):
            return
        path = self.get_argument("path", "").strip()
        dest = self.get_argument("dest", "").strip()
        if not path or not dest:
            self.set_status(400)
            self.write(INVALID_REQUEST_PATH_DEST)
            return
        src_abs = os.path.abspath(os.path.join(ROOT_DIR, path))
        dest_abs = os.path.abspath(os.path.join(ROOT_DIR, dest))
        if not is_within_root(src_abs, ROOT_DIR) or not is_within_root(
            dest_abs, ROOT_DIR
        ):
            self.set_status(403)
            self.write(ACCESS_DENIED_SHORT)
            return
        if not os.path.exists(src_abs):
            self.set_status(404)
            self.write(SOURCE_NOT_FOUND)
            return
        if os.path.exists(dest_abs):
            self.set_status(409)
            self.write(DESTINATION_EXISTS)
            return
        try:
            shutil.move(src_abs, dest_abs)
        except OSError as e:
            logging.error("Move error: %s", e)
            self.set_status(500)
            self.write(MOVE_FAILED)
            return
        log_audit(
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


class BulkHandler(BaseHandler):
    @tornado.web.authenticated
    def post(self):
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
        root = ROOT_DIR
        results = {"ok": True, "results": []}
        remote_ip = self.request.remote_ip
        for path in paths:
            if not isinstance(path, str):
                results["results"].append(
                    {"path": path, "ok": False, "error": INVALID_PATH}
                )
                continue
            path = path.strip().strip("/")
            abspath = os.path.abspath(os.path.join(ROOT_DIR, path))
            if not is_within_root(abspath, root):
                results["results"].append(
                    {"path": path, "ok": False, "error": ACCESS_DENIED_LOWER}
                )
                continue
            if not os.path.exists(abspath):
                results["results"].append(
                    {"path": path, "ok": False, "error": NOT_FOUND_LOWER}
                )
                continue
            err = _process_bulk_action(
                action,
                abspath,
                path,
                data,
                self.db_conn,
                self.get_display_username,
                remote_ip,
            )
            if err:
                results["ok"] = False
                results["results"].append({"path": path, "ok": False, "error": err})
            else:
                results["results"].append({"path": path, "ok": True})
        self.set_header("Content-Type", HEADER_APPLICATION_JSON)
        self.write(results)


class EditHandler(BaseHandler):
    @tornado.web.authenticated
    def post(self):
        if not self.require_feature("file_edit", True, body=FILE_EDIT_DISABLED):
            return

        # Accept both JSON and form-encoded bodies
        content_type = self.request.headers.get("Content-Type", "")
        path = ""
        content = ""
        if content_type.startswith(HEADER_APPLICATION_JSON):
            try:
                data = json.loads(
                    self.request.body.decode("utf-8", errors="replace") or "{}"
                )
                path = data.get("path", "")
                content = data.get("content", "")
            except Exception:
                self.set_status(400)
                self.write(INVALID_JSON_REQUEST)
                return
        else:
            path = self.get_argument("path", "")
            content = self.get_argument("content", "")

        abspath = (pathlib.Path(ROOT_DIR) / ("." + path)).absolute().resolve()

        if not is_within_root(str(abspath), ROOT_DIR):
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
            # Safe write: write to temp file in same directory then replace atomically
            directory_name = os.path.dirname(abspath)
            os.makedirs(directory_name, exist_ok=True)
            # Use delete=False to prevent file deletion before os.replace can use it
            temp_fd = tempfile.NamedTemporaryFile(
                "w", encoding="utf-8", dir=directory_name, delete=False
            )
            temp_path = temp_fd.name
            try:
                temp_fd.write(content)
                temp_fd.close()
                os.replace(temp_path, abspath)
            except Exception:
                # Clean up temp file on failure
                try:
                    os.unlink(temp_path)
                except OSError:
                    pass
                raise
            log_audit(
                self.db_conn,
                "file_edit",
                username=self.get_display_username(),
                details=path_to_rel(str(abspath)),
                ip=self.request.remote_ip,
            )
            self.set_status(200)
            # Respond JSON if requested
            if self.request.headers.get("Accept") == HEADER_APPLICATION_JSON:
                self.write({"ok": True})
            else:
                self.write(FILE_SAVED_SUCCESSFULLY)
        except Exception as e:
            logging.error(f"File save error: {e}")
            self.set_status(500)
            self.write(FILE_SAVE_ERROR)


class CloudUploadHandler(BaseHandler):
    @tornado.web.authenticated
    async def post(self, provider_name: str):
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
        except CloudProviderError as exc:
            self.set_status(400)
            self.write({"error": str(exc)})
            return
        except Exception:
            logging.exception(
                "Failed to upload file to cloud provider %s", provider_name
            )
            self.set_status(500)
            self.write({"error": CLOUD_UPLOAD_FAILED})
            return

        self.write({"file": cloud_file.to_dict()})
