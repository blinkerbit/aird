"""File mutation and listing API routes."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil

from starlette.background import BackgroundTask
from starlette.requests import Request
from starlette.responses import FileResponse, Response
from starlette.routing import Route

from aird.constants.input_limits import MAX_BULK_JSON_BYTES, MAX_BULK_PATHS
from aird.constants.file_ops import (
    ACCESS_DENIED,
    ACCESS_DENIED_SHORT,
    CONFLICT_EXISTS,
    COPY_DISABLED,
    COPY_FAILED,
    DESTINATION_EXISTS,
    FILE_DELETE_DISABLED,
    FILE_OR_FOLDER_NOT_FOUND,
    FILE_RENAME_DISABLED,
    FILE_NOT_FOUND,
    FOLDER_CREATE_DISABLED,
    FOLDER_CREATE_FAILED,
    FOLDER_DELETE_DISABLED,
    FOLDER_NAME_TOO_LONG,
    FOLDER_NOT_EMPTY,
    FILENAME_TOO_LONG,
    INVALID_FILENAME,
    INVALID_FOLDER_NAME,
    INVALID_REQUEST_PATH_AND_NAME,
    INVALID_REQUEST_PATH_DEST,
    MOVE_DISABLED,
    MOVE_FAILED,
    RENAME_FAILED,
    SOURCE_NOT_FOUND,
)
from aird.core.security import is_within_root
from aird.core.zip_download import ZipDownloadError, build_zip_file, collect_zip_entries
from aird.handlers.file_op_handlers import path_to_rel
from aird.ms.ctx import check_xsrf_token, user_root_for
from aird.ms.routers._helpers import (
    db_conn,
    get_service,
    has_modify_privileges,
    json_response,
    request_mounts,
    require_feature,
    require_user,
    resolve_request_rel,
    text_response,
    wants_json,
)
from aird.utils.util import (
    augment_with_shared_status,
    get_file_size_safe,
    get_files_in_directory,
    is_audio_file,
    is_feature_enabled,
    is_video_file,
)

logger = logging.getLogger(__name__)

FILES_URL = "/files/"


def _deny_write(request: Request, user: dict, *rels, as_target: bool = False):
    from aird.core.browse_paths import write_blocked_reason

    mounts = request_mounts(request, user)
    for rel in rels:
        if write_blocked_reason(rel, mounts, as_target=as_target):
            return text_response(403, ACCESS_DENIED)
    return None


def _remote_ip(request: Request) -> str | None:
    return request.client.host if request.client else None


async def _read_form(request: Request) -> dict[str, str]:
    form = await request.form()
    out: dict[str, str] = {}
    for key in form.keys():
        val = form.get(key)
        if val is not None:
            out[str(key)] = str(val)
    return out


async def _post_guard(request: Request) -> tuple[dict | Response, dict[str, str] | None]:
    user = require_user(request)
    if isinstance(user, Response):
        return user, None
    if not has_modify_privileges(user):
        return text_response(403, "Write access denied. Sign in with modify privileges."), None
    form = await _read_form(request)
    try:
        check_xsrf_token(request, form.get("_xsrf"))
    except PermissionError:
        return text_response(403, "XSRF validation failed"), None
    return user, form


async def mkdir(request: Request) -> Response:
    user, form = await _post_guard(request)
    if isinstance(user, Response):
        return user
    if form is None:
        return text_response(403, "XSRF validation failed")

    disabled = require_feature("folder_create", True, FOLDER_CREATE_DISABLED)
    if disabled:
        return disabled

    parent = form.get("parent", "").strip().strip("/")
    name = form.get("name", "").strip()
    if not name or name in (".", "..") or "/" in name or "\\" in name:
        return text_response(400, INVALID_FOLDER_NAME)
    if len(name) > 255:
        return text_response(400, FOLDER_NAME_TOO_LONG)

    user_root = user_root_for(user)
    blocked = _deny_write(request, user, parent)
    if blocked:
        return blocked
    parent_abs, confine = resolve_request_rel(request, user, parent)
    new_rel = f"{parent}/{name}" if parent else name
    new_dir_abs, new_confine = resolve_request_rel(request, user, new_rel)
    if (
        not parent_abs
        or not confine
        or not new_dir_abs
        or not new_confine
        or not is_within_root(parent_abs, confine)
        or not is_within_root(new_dir_abs, new_confine)
    ):
        return text_response(403, ACCESS_DENIED)
    if os.path.exists(new_dir_abs):
        return text_response(409, CONFLICT_EXISTS)

    try:
        os.makedirs(new_dir_abs, exist_ok=False)
    except OSError:
        logger.exception("CreateFolder error")
        return text_response(500, FOLDER_CREATE_FAILED)

    audit = get_service(request, "audit_service")
    if audit:
        audit.log(
            db_conn(request),
            "folder_create",
            username=user.get("username"),
            details=path_to_rel(new_dir_abs, user_root),
            ip=_remote_ip(request),
        )

    rel = f"{parent}/{name}" if parent else name
    if wants_json(request):
        return json_response({"ok": True, "path": rel})
    return Response(status_code=302, headers={"Location": FILES_URL + rel + "/"})


async def delete_path(request: Request) -> Response:
    user, form = await _post_guard(request)
    if isinstance(user, Response):
        return user
    if form is None:
        return text_response(403, "XSRF validation failed")

    path = form.get("path", "")
    blocked = _deny_write(request, user, path, as_target=True)
    if blocked:
        return blocked
    user_root = user_root_for(user)
    abspath, confine = resolve_request_rel(request, user, path)
    if not abspath or not confine or not is_within_root(abspath, confine):
        return text_response(403, ACCESS_DENIED)

    conn = db_conn(request)
    audit = get_service(request, "audit_service")

    if os.path.isdir(abspath):
        disabled = require_feature("folder_delete", True, FOLDER_DELETE_DISABLED)
        if disabled:
            return disabled
        recursive = form.get("recursive", "0") == "1"
        if not recursive and os.listdir(abspath):
            return text_response(400, FOLDER_NOT_EMPTY)
        shutil.rmtree(abspath)
        if audit:
            audit.log(
                conn,
                "folder_delete",
                username=user.get("username"),
                details=path_to_rel(abspath, user_root),
                ip=_remote_ip(request),
            )
    elif os.path.isfile(abspath):
        disabled = require_feature("file_delete", True, FILE_DELETE_DISABLED)
        if disabled:
            return disabled
        file_size = get_file_size_safe(abspath)
        os.remove(abspath)
        if is_feature_enabled("storage_quotas", False) and file_size > 0:
            quota = get_service(request, "quota_service")
            if quota:
                quota.update_used_bytes(conn, user.get("username"), -file_size)
        if audit:
            audit.log(
                conn,
                "file_delete",
                username=user.get("username"),
                details=path_to_rel(abspath, user_root),
                ip=_remote_ip(request),
            )
    else:
        return text_response(404, FILE_OR_FOLDER_NOT_FOUND)

    parent = os.path.dirname(path)
    if wants_json(request):
        return json_response({"ok": True})
    loc = FILES_URL + parent if parent else FILES_URL
    return Response(status_code=302, headers={"Location": loc})


async def rename_path(request: Request) -> Response:
    user, form = await _post_guard(request)
    if isinstance(user, Response):
        return user
    if form is None:
        return text_response(403, "XSRF validation failed")

    disabled = require_feature("file_rename", True, FILE_RENAME_DISABLED)
    if disabled:
        return disabled

    path = form.get("path", "").strip()
    new_name = form.get("new_name", "").strip()
    if not path or not new_name:
        return text_response(400, INVALID_REQUEST_PATH_AND_NAME)
    if new_name in (".", "..") or "/" in new_name or "\\" in new_name:
        return text_response(400, INVALID_FILENAME)
    if len(new_name) > 255:
        return text_response(400, FILENAME_TOO_LONG)

    user_root = user_root_for(user)
    blocked = _deny_write(request, user, path, as_target=True)
    if blocked:
        return blocked
    parent_rel = os.path.dirname(path.replace("\\", "/")).replace("\\", "/")
    dest_rel = f"{parent_rel}/{new_name}" if parent_rel else new_name
    blocked = _deny_write(request, user, parent_rel)
    if blocked:
        return blocked
    abspath, confine = resolve_request_rel(request, user, path)
    new_abspath, new_confine = resolve_request_rel(request, user, dest_rel)
    if (
        not abspath
        or not confine
        or not new_abspath
        or not new_confine
        or not is_within_root(abspath, confine)
        or not is_within_root(new_abspath, new_confine)
    ):
        return text_response(403, ACCESS_DENIED)
    if not os.path.exists(abspath):
        return text_response(404, FILE_NOT_FOUND)

    try:
        os.rename(abspath, new_abspath)
    except OSError:
        return text_response(500, RENAME_FAILED)

    audit = get_service(request, "audit_service")
    if audit:
        audit.log(
            db_conn(request),
            "rename",
            username=user.get("username"),
            details=f"{path} -> {new_name}",
            ip=_remote_ip(request),
        )

    parent = os.path.dirname(path)
    if wants_json(request):
        return json_response({"ok": True})
    loc = FILES_URL + parent if parent else FILES_URL
    return Response(status_code=302, headers={"Location": loc})


def _resolve_copy_move(
    request: Request, user: dict, path: str, dest: str
) -> tuple[str, str] | Response:
    if not path or not dest:
        return text_response(400, INVALID_REQUEST_PATH_DEST)
    user_root = user_root_for(user)
    blocked = _deny_write(request, user, path, as_target=True)
    if blocked:
        return blocked
    blocked = _deny_write(request, user, dest)
    if blocked:
        return blocked
    src_abs, src_confine = resolve_request_rel(request, user, path)
    dest_abs, dest_confine = resolve_request_rel(request, user, dest)
    if (
        not src_abs
        or not src_confine
        or not dest_abs
        or not dest_confine
        or not is_within_root(src_abs, src_confine)
        or not is_within_root(dest_abs, dest_confine)
    ):
        return text_response(403, ACCESS_DENIED_SHORT)
    if not os.path.exists(src_abs):
        return text_response(404, SOURCE_NOT_FOUND)
    if os.path.exists(dest_abs):
        return text_response(409, DESTINATION_EXISTS)
    return src_abs, dest_abs


async def copy_path(request: Request) -> Response:
    user, form = await _post_guard(request)
    if isinstance(user, Response):
        return user
    if form is None:
        return text_response(403, "XSRF validation failed")

    disabled = require_feature("file_rename", True, COPY_DISABLED)
    if disabled:
        return disabled

    path = form.get("path", "").strip()
    dest = form.get("dest", "").strip()
    resolved = _resolve_copy_move(request, user, path, dest)
    if isinstance(resolved, Response):
        return resolved
    src_abs, dest_abs = resolved

    try:
        if os.path.isdir(src_abs):
            shutil.copytree(src_abs, dest_abs)
        else:
            shutil.copy2(src_abs, dest_abs)
    except OSError:
        logger.exception("Copy error")
        return text_response(500, COPY_FAILED)

    audit = get_service(request, "audit_service")
    if audit:
        audit.log(
            db_conn(request),
            "copy",
            username=user.get("username"),
            details=f"{path} -> {dest}",
            ip=_remote_ip(request),
        )

    if wants_json(request):
        return json_response({"ok": True})
    parent = os.path.dirname(dest)
    loc = FILES_URL + parent if parent else FILES_URL
    return Response(status_code=302, headers={"Location": loc})


async def move_path(request: Request) -> Response:
    user, form = await _post_guard(request)
    if isinstance(user, Response):
        return user
    if form is None:
        return text_response(403, "XSRF validation failed")

    disabled = require_feature("file_rename", True, MOVE_DISABLED)
    if disabled:
        return disabled

    path = form.get("path", "").strip()
    dest = form.get("dest", "").strip()
    resolved = _resolve_copy_move(request, user, path, dest)
    if isinstance(resolved, Response):
        return resolved
    src_abs, dest_abs = resolved

    try:
        shutil.move(src_abs, dest_abs)
    except OSError:
        logger.exception("Move error")
        return text_response(500, MOVE_FAILED)

    audit = get_service(request, "audit_service")
    if audit:
        audit.log(
            db_conn(request),
            "move",
            username=user.get("username"),
            details=f"{path} -> {dest}",
            ip=_remote_ip(request),
        )

    if wants_json(request):
        return json_response({"ok": True})
    parent = os.path.dirname(dest)
    loc = FILES_URL + parent if parent else FILES_URL
    return Response(status_code=302, headers={"Location": loc})


async def api_files(request: Request) -> Response:
    user = require_user(request)
    if isinstance(user, Response):
        return user

    path = request.path_params.get("path", "").strip().strip("/")
    user_root = user_root_for(user)
    abspath, confine = resolve_request_rel(request, user, path)
    if not abspath or not confine or not is_within_root(abspath, confine):
        return text_response(403, "Access denied")
    if not os.path.isdir(abspath):
        return text_response(404, "Directory not found")

    try:
        files = get_files_in_directory(abspath)
        conn = db_conn(request)
        if not path:
            from aird.core.browse_paths import overlay_mount_entries

            overlay_mount_entries(files, request_mounts(request, user), path)
        if conn:
            augment_with_shared_status(
                files,
                path,
                get_service(request, "share_service").list_shares(conn),
                db_conn=conn,
                root_dir=user_root,
                viewer_username=user.get("username"),
                viewer_is_admin=str(user.get("role", "")).lower() == "admin",
            )
        return json_response(
            {
                "path": path,
                "files": files,
                "is_video": is_video_file(path),
                "is_audio": is_audio_file(path),
            }
        )
    except Exception:
        logger.exception("Error listing files")
        return text_response(500, "Internal server error")


async def api_folder_size(request: Request) -> Response:
    from aird.core.folder_size import (
        compute_folder_size,
        norm_rel_path,
        resolve_folder_abspath,
    )
    from aird.utils.util import format_size

    user = require_user(request)
    if isinstance(user, Response):
        return user

    path = request.query_params.get("path", "").strip()
    norm_path = norm_rel_path(path)
    user_root = user_root_for(user)
    abs_path = resolve_folder_abspath(user_root, path, request_mounts(request, user))
    if not abs_path:
        return json_response({"error": "Folder not found"}, status=404)

    try:
        total_bytes, file_count = await asyncio.to_thread(compute_folder_size, abs_path)
    except Exception:
        logger.exception("Folder size calculation failed for %s", norm_path)
        return json_response({"error": "Folder size calculation failed"}, status=500)

    return json_response(
        {
            "path": norm_path,
            "bytes": total_bytes,
            "files": file_count,
            "size_str": format_size(total_bytes),
        }
    )


async def download_zip(request: Request) -> Response:
    user = require_user(request)
    if isinstance(user, Response):
        return user
    disabled = require_feature("file_download", True, "File download is disabled")
    if disabled:
        return disabled
    raw = await request.body()
    if len(raw) > MAX_BULK_JSON_BYTES:
        return text_response(400, "Bad request")
    try:
        data = json.loads(raw.decode("utf-8") or "{}")
    except json.JSONDecodeError:
        return text_response(400, "Invalid JSON")
    try:
        check_xsrf_token(request, data.get("_xsrf"))
    except PermissionError:
        return text_response(403, "XSRF validation failed")
    paths = data.get("paths")
    if not isinstance(paths, list) or not paths or len(paths) > MAX_BULK_PATHS:
        return text_response(400, "paths is required")
    root = user_root_for(user)
    mounts = request_mounts(request, user)
    try:
        entries = await asyncio.to_thread(collect_zip_entries, root, paths, mounts)
        archive = await asyncio.to_thread(build_zip_file, entries)
    except ZipDownloadError as exc:
        return text_response(exc.status, str(exc))
    except Exception:
        logger.exception("zip download failed")
        return text_response(500, "Failed to create zip archive")
    filename = os.path.basename(
        str(data.get("filename") or "aird-download.zip").strip()
    )
    if not filename.lower().endswith(".zip"):
        filename += ".zip"
    return FileResponse(
        archive,
        filename=filename,
        media_type="application/zip",
        background=BackgroundTask(os.remove, archive),
    )


routes = [
    Route("/mkdir", mkdir, methods=["POST"]),
    Route("/delete", delete_path, methods=["POST"]),
    Route("/rename", rename_path, methods=["POST"]),
    Route("/copy", copy_path, methods=["POST"]),
    Route("/move", move_path, methods=["POST"]),
    Route("/api/files/{path:path}", api_files, methods=["GET"]),
    Route("/api/folder-size", api_folder_size, methods=["GET"]),
    Route("/api/download/zip", download_zip, methods=["POST"]),
]
