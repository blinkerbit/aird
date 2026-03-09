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
from aird.utils.util import is_within_root, is_feature_enabled, sanitize_cloud_filename
from aird.db import log_audit
import aird.constants as constants_module
from aird.config import (
    ROOT_DIR,
    ALLOWED_UPLOAD_EXTENSIONS,
    CLOUD_MANAGER,
)
from aird.cloud import CloudManager, CloudProviderError
from io import BytesIO


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
            self._reject_reason = "File upload is disabled."
            return

        # Read and decode headers provided by client
        self.upload_dir = self.request.headers.get("X-Upload-Dir", "")
        self.filename = self.request.headers.get("X-Upload-Filename", "")

        # Basic validation
        if not self.filename:
            self._reject = True
            self._reject_reason = "Missing X-Upload-Filename header"
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
        # If uploads disabled, return now
        if not is_feature_enabled("file_upload", True):
            self.set_status(403)
            self.write("Feature disabled: File upload is currently disabled by administrator")
            return

        # If we rejected in prepare (bad/missing headers), report
        if self._reject:
            self.set_status(400)
            self.write(self._reject_reason or "Bad request")
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
            self.write(f"File too large: Please choose a file smaller than {limit_mb} MB")
            return

        # Enhanced path validation
        safe_dir_abs = os.path.realpath(os.path.join(ROOT_DIR, self.upload_dir.strip("/")))
        if not is_within_root(safe_dir_abs, ROOT_DIR):
            self.set_status(403)
            self.write("Access denied: This path is not allowed for security reasons")
            return

        # Validate filename more strictly
        safe_filename = os.path.basename(self.filename)
        if not safe_filename or safe_filename in ['.', '..']:
            self.set_status(400)
            self.write("Invalid filename: Please use a valid filename without special characters")
            return
            
        # Enforce allowed extensions unless admin enabled "allow all file types"
        allow_all = constants_module.UPLOAD_CONFIG.get("allow_all_file_types", 0)
        if not allow_all:
            file_ext = os.path.splitext(safe_filename)[1].lower()
            allowed_set = getattr(constants_module, "UPLOAD_ALLOWED_EXTENSIONS", None) or ALLOWED_UPLOAD_EXTENSIONS
            if file_ext not in allowed_set:
                self.set_status(415)
                self.write("Unsupported file type: This file type is not allowed for upload")
                return
            
        # Validate filename length
        if len(safe_filename) > 255:
            self.set_status(400)
            self.write("Filename too long: Please use a shorter filename")
            return

        final_path_abs = os.path.realpath(os.path.join(safe_dir_abs, safe_filename))
        if not is_within_root(final_path_abs, safe_dir_abs):
            self.set_status(403)
            self.write("Access denied: This path is not allowed for security reasons")
            return

        os.makedirs(os.path.dirname(final_path_abs), exist_ok=True)

        try:
            shutil.move(self._temp_path, final_path_abs)
            self._moved = True
        except Exception as e:
            logging.error(f"Upload save failed: {e}")
            self.set_status(500)
            self.write("Failed to save upload. Please try again.")
            return

        log_audit(constants_module.DB_CONN, "file_upload", username=self.get_display_username(), details=path_to_rel(final_path_abs), ip=self.request.remote_ip)
        self.set_status(200)
        self.write("Upload successful")

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
        if not is_feature_enabled("folder_create", True):
            self.set_status(403)
            self.write("Feature disabled: Create folder is currently disabled by administrator")
            return
        parent = self.get_argument("parent", "").strip().strip("/")
        name = self.get_argument("name", "").strip()
        if not name or name in (".", "..") or "/" in name or "\\" in name:
            self.set_status(400)
            self.write("Invalid request: Folder name is required and must not contain / or \\")
            return
        if len(name) > 255:
            self.set_status(400)
            self.write("Folder name too long")
            return
        parent_abs = os.path.abspath(os.path.join(ROOT_DIR, parent)) if parent else ROOT_DIR
        new_dir_abs = os.path.abspath(os.path.join(parent_abs, name))
        if not is_within_root(parent_abs, ROOT_DIR) or not is_within_root(new_dir_abs, ROOT_DIR):
            self.set_status(403)
            self.write("Access denied: You don't have permission to perform this action")
            return
        if os.path.exists(new_dir_abs):
            self.set_status(409)
            self.write("Conflict: A file or folder with that name already exists")
            return
        try:
            os.makedirs(new_dir_abs, exist_ok=False)
        except OSError as e:
            logging.error("CreateFolder error: %s", e)
            self.set_status(500)
            self.write("Failed to create folder")
            return
        username = self.get_display_username() if hasattr(self, "get_display_username") else None
        log_audit(constants_module.DB_CONN, "folder_create", username=username, details=path_to_rel(new_dir_abs), ip=self.request.remote_ip)
        if self.request.headers.get("Accept") == "application/json":
            self.set_header("Content-Type", "application/json")
            self.write({"ok": True, "path": (parent + "/" + name) if parent else name})
            return
        self.redirect("/files/" + ((parent + "/" + name) if parent else name) + "/")


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
            self.write("Access denied: You don't have permission to perform this action")
            return
        if os.path.isdir(abspath):
            if not is_feature_enabled("folder_delete", True):
                self.set_status(403)
                self.write("Feature disabled: Folder deletion is currently disabled by administrator")
                return
            recursive = self.get_argument("recursive", "0") == "1"
            if not recursive and os.listdir(abspath):
                self.set_status(400)
                self.write("Folder is not empty: use recursive=1 to delete anyway")
                return
            shutil.rmtree(abspath)
            log_audit(constants_module.DB_CONN, "folder_delete", username=self.get_display_username(), details=path_to_rel(abspath), ip=self.request.remote_ip)
        elif os.path.isfile(abspath):
            if not is_feature_enabled("file_delete", True):
                self.set_status(403)
                self.write("Feature disabled: File deletion is currently disabled by administrator")
                return
            os.remove(abspath)
            log_audit(constants_module.DB_CONN, "file_delete", username=self.get_display_username(), details=path_to_rel(abspath), ip=self.request.remote_ip)
        else:
            self.set_status(404)
            self.write("File or folder not found")
            return
        parent = os.path.dirname(path)
        if self.request.headers.get("Accept") == "application/json":
            self.set_header("Content-Type", "application/json")
            self.write({"ok": True})
            return
        self.redirect("/files/" + parent if parent else "/files/")

class RenameHandler(BaseHandler):
    @tornado.web.authenticated
    def post(self):
        if not is_feature_enabled("file_rename", True):
            self.set_status(403)
            self.write("Feature disabled: File renaming is currently disabled by administrator")
            return

        path = self.get_argument("path", "").strip()
        new_name = self.get_argument("new_name", "").strip()
        
        # Input validation
        if not path or not new_name:
            self.set_status(400)
            self.write("Invalid request: Both file path and new name are required")
            return
            
        # Validate new filename
        if new_name in ['.', '..'] or '/' in new_name or '\\' in new_name:
            self.set_status(400)
            self.write("Invalid filename: Please use a valid filename without special characters")
            return
            
        if len(new_name) > 255:
            self.set_status(400)
            self.write("Filename too long: Please use a shorter filename")
            return
        
        abspath = os.path.abspath(os.path.join(ROOT_DIR, path))
        new_abspath = os.path.abspath(os.path.join(ROOT_DIR, os.path.dirname(path), new_name))
        root = ROOT_DIR
        if not (is_within_root(abspath, root) and is_within_root(new_abspath, root)):
            self.set_status(403)
            self.write("Access denied: You don't have permission to perform this action")
            return
            
        if not os.path.exists(abspath):
            self.set_status(404)
            self.write("File not found: The requested file may have been moved or deleted")
            return
            
        try:
            os.rename(abspath, new_abspath)
        except OSError:
            self.set_status(500)
            self.write("Operation failed: Unable to rename the file")
            return

        log_audit(constants_module.DB_CONN, "rename", username=self.get_display_username(), details=f"{path} -> {new_name}", ip=self.request.remote_ip)
        parent = os.path.dirname(path)
        if self.request.headers.get("Accept") == "application/json":
            self.set_header("Content-Type", "application/json")
            self.write({"ok": True})
            return
        self.redirect("/files/" + parent if parent else "/files/")


class CopyHandler(BaseHandler):
    @tornado.web.authenticated
    def post(self):
        if not is_feature_enabled("file_rename", True):
            self.set_status(403)
            self.write("Feature disabled: Copy is currently disabled by administrator")
            return
        path = self.get_argument("path", "").strip()
        dest = self.get_argument("dest", "").strip()
        if not path or not dest:
            self.set_status(400)
            self.write("Invalid request: path and dest are required")
            return
        src_abs = os.path.abspath(os.path.join(ROOT_DIR, path))
        dest_abs = os.path.abspath(os.path.join(ROOT_DIR, dest))
        if not is_within_root(src_abs, ROOT_DIR) or not is_within_root(dest_abs, ROOT_DIR):
            self.set_status(403)
            self.write("Access denied")
            return
        if not os.path.exists(src_abs):
            self.set_status(404)
            self.write("Source not found")
            return
        if os.path.exists(dest_abs):
            self.set_status(409)
            self.write("Destination already exists")
            return
        try:
            if os.path.isdir(src_abs):
                shutil.copytree(src_abs, dest_abs)
            else:
                shutil.copy2(src_abs, dest_abs)
        except OSError as e:
            logging.error("Copy error: %s", e)
            self.set_status(500)
            self.write("Copy failed")
            return
        log_audit(constants_module.DB_CONN, "copy", username=self.get_display_username(), details=f"{path} -> {dest}", ip=self.request.remote_ip)
        if self.request.headers.get("Accept") == "application/json":
            self.set_header("Content-Type", "application/json")
            self.write({"ok": True})
            return
        self.redirect("/files/" + os.path.dirname(dest) if os.path.dirname(dest) else "/files/")


class MoveHandler(BaseHandler):
    @tornado.web.authenticated
    def post(self):
        if not is_feature_enabled("file_rename", True):
            self.set_status(403)
            self.write("Feature disabled: Move is currently disabled by administrator")
            return
        path = self.get_argument("path", "").strip()
        dest = self.get_argument("dest", "").strip()
        if not path or not dest:
            self.set_status(400)
            self.write("Invalid request: path and dest are required")
            return
        src_abs = os.path.abspath(os.path.join(ROOT_DIR, path))
        dest_abs = os.path.abspath(os.path.join(ROOT_DIR, dest))
        if not is_within_root(src_abs, ROOT_DIR) or not is_within_root(dest_abs, ROOT_DIR):
            self.set_status(403)
            self.write("Access denied")
            return
        if not os.path.exists(src_abs):
            self.set_status(404)
            self.write("Source not found")
            return
        if os.path.exists(dest_abs):
            self.set_status(409)
            self.write("Destination already exists")
            return
        try:
            shutil.move(src_abs, dest_abs)
        except OSError as e:
            logging.error("Move error: %s", e)
            self.set_status(500)
            self.write("Move failed")
            return
        log_audit(constants_module.DB_CONN, "move", username=self.get_display_username(), details=f"{path} -> {dest}", ip=self.request.remote_ip)
        if self.request.headers.get("Accept") == "application/json":
            self.set_header("Content-Type", "application/json")
            self.write({"ok": True})
            return
        self.redirect("/files/" + os.path.dirname(dest) if os.path.dirname(dest) else "/files/")


class BulkHandler(BaseHandler):
    @tornado.web.authenticated
    def post(self):
        try:
            data = json.loads(self.request.body.decode("utf-8", errors="replace") or "{}")
        except Exception:
            self.set_status(400)
            self.write("Invalid JSON")
            return
        action = (data.get("action") or "").strip().lower()
        paths = data.get("paths")
        if not isinstance(paths, list) or not paths:
            self.set_status(400)
            self.write("paths must be a non-empty array")
            return
        root = ROOT_DIR
        results = {"ok": True, "results": []}
        for path in paths:
            if not isinstance(path, str):
                results["results"].append({"path": path, "ok": False, "error": "invalid path"})
                continue
            path = path.strip().strip("/")
            abspath = os.path.abspath(os.path.join(ROOT_DIR, path))
            if not is_within_root(abspath, root):
                results["results"].append({"path": path, "ok": False, "error": "access denied"})
                continue
            if not os.path.exists(abspath):
                results["results"].append({"path": path, "ok": False, "error": "not found"})
                continue
            err = None
            if action == "delete":
                if os.path.isdir(abspath):
                    if not is_feature_enabled("folder_delete", True):
                        err = "folder delete disabled"
                    else:
                        try:
                            shutil.rmtree(abspath)
                            log_audit(constants_module.DB_CONN, "folder_delete", username=self.get_display_username(), details=path_to_rel(abspath), ip=self.request.remote_ip)
                        except OSError as e:
                            err = str(e)
                else:
                    if not is_feature_enabled("file_delete", True):
                        err = "file delete disabled"
                    else:
                        try:
                            os.remove(abspath)
                            log_audit(constants_module.DB_CONN, "file_delete", username=self.get_display_username(), details=path_to_rel(abspath), ip=self.request.remote_ip)
                        except OSError as e:
                            err = str(e)
            elif action == "add_to_share":
                share_id = data.get("share_id")
                if not share_id:
                    err = "share_id required"
                else:
                    from aird.db import get_share_by_id, update_share
                    conn = constants_module.DB_CONN
                    if not conn:
                        err = "database unavailable"
                    else:
                        share = get_share_by_id(conn, share_id)
                        if not share:
                            err = "share not found"
                        else:
                            paths_list = list(share.get("paths") or [])
                            rel = path_to_rel(abspath)
                            if rel not in paths_list:
                                paths_list.append(rel)
                                if update_share(conn, share_id, paths=paths_list):
                                    log_audit(constants_module.DB_CONN, "share_update", username=self.get_display_username(), details=f"add path to {share_id}: {rel}", ip=self.request.remote_ip)
                                else:
                                    err = "update failed"
            else:
                err = "unsupported action (use delete or add_to_share)"
            if err:
                results["ok"] = False
                results["results"].append({"path": path, "ok": False, "error": err})
            else:
                results["results"].append({"path": path, "ok": True})
        self.set_header("Content-Type", "application/json")
        self.write(results)


class EditHandler(BaseHandler):
    @tornado.web.authenticated
    def post(self):
        if not is_feature_enabled("file_edit", True):
            self.set_status(403)
            self.write("Feature disabled: File editing is currently disabled by administrator")
            return

        # Accept both JSON and form-encoded bodies
        content_type = self.request.headers.get("Content-Type", "")
        path = ""
        content = ""
        if content_type.startswith("application/json"):
            try:
                data = json.loads(self.request.body.decode("utf-8", errors="replace") or "{}")
                path = data.get("path", "")
                content = data.get("content", "")
            except Exception:
                self.set_status(400)
                self.write("Invalid request: Please provide valid JSON data")
                return
        else:
            path = self.get_argument("path", "")
            content = self.get_argument("content", "")
        
        abspath = (pathlib.Path(ROOT_DIR) /  ("."+path)).absolute().resolve()
        
        if not is_within_root(str(abspath), ROOT_DIR):
            logging.warning(f"EditHandler: access denied for path {path}.")
            self.set_status(403)
            self.write("Access denied: You don't have permission to perform this action.")
            return
            
        if not os.path.isfile(abspath):
            logging.warning(f"EditHandler: file not found at path {path}.")
            self.set_status(404)
            self.write("File not found: The requested file may have been moved or deleted")
            return

        try:
            # Safe write: write to temp file in same directory then replace atomically
            directory_name = os.path.dirname(abspath)
            os.makedirs(directory_name, exist_ok=True)
            # Use delete=False to prevent file deletion before os.replace can use it
            temp_fd = tempfile.NamedTemporaryFile('w', encoding='utf-8', dir=directory_name, delete=False)
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
            log_audit(constants_module.DB_CONN, "file_edit", username=self.get_display_username(), details=path_to_rel(str(abspath)), ip=self.request.remote_ip)
            self.set_status(200)
            # Respond JSON if requested
            if self.request.headers.get('Accept') == 'application/json':
                self.write({"ok": True})
            else:
                self.write("File saved successfully.")
        except Exception as e:
            logging.error(f"File save error: {e}")
            self.set_status(500)
            self.write("Error saving file. Please try again.")

class CloudUploadHandler(BaseHandler):
    @tornado.web.authenticated
    async def post(self, provider_name: str):
        manager: CloudManager = self.application.settings.get("cloud_manager", CLOUD_MANAGER)
        provider = manager.get(provider_name)
        if not provider:
            self.set_status(404)
            self.write({"error": "Provider not configured"})
            return

        uploads = self.request.files.get("file")
        if not uploads:
            self.set_status(400)
            self.write({"error": "No file uploaded"})
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
            self.write({"error": "File too large for upload"})
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
            logging.exception("Failed to upload file to cloud provider %s", provider_name)
            self.set_status(500)
            self.write({"error": "Failed to upload file"})
            return

        self.write({"file": cloud_file.to_dict()})

