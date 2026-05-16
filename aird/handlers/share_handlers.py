import tornado.web
import json
import secrets
import time
from datetime import datetime, timezone
import os
import logging

from aird.handlers.base_handler import (
    BaseHandler,
    XSRFTokenMixin,
    get_user_root,
    get_username_string_for_db,
    login_matches_share_creator_field,
    require_action,
    require_db,
    require_modify_access,
)
from aird.core.events import ShareCreatedEvent, now_ts
from aird.domain.contracts import ShareCreateRequest, ShareCreateResponse
from aird.constants.input_limits import ACCESS_TOKEN_MAX_LEN, SHARE_JSON_BODY_MAX_BYTES, SHARE_ID_MAX_LEN
from aird.core.input_validation import (
    validate_share_create_struct,
    validate_share_update_struct,
)
from aird.utils.util import (
    get_all_files_recursive,
    filter_files_by_patterns,
    is_cloud_relative_path,
    remove_cloud_file_if_exists,
    cleanup_share_cloud_dir_if_empty,
    remove_share_cloud_dir,
    download_cloud_items,
)
from aird.core.security import (
    is_within_root,
)
from aird.handlers.constants import (
    FS_DISABLED_MSG,
    ACCESS_TOKEN_INVALID_OR_EXPIRED,
    INVALID_SHARE_LINK,
    CLOUD_DOWNLOAD_FAILED,
)
from aird.core.share_root import filesystem_root_for_share
from aird.db.shares import list_files_for_tag_share, share_covers_relative_path
from aird import constants as constants_module
from aird.cloud import CloudProviderError
from aird.handlers.view_handlers import MainHandler

# ---------------------------------------------------------------------------
# Helpers for share creation (reduce cognitive complexity)
# ---------------------------------------------------------------------------


def _normalize_path_entry(entry):
    """Extract local path from entry; return (path_str, is_cloud). (None, True) for cloud."""
    if isinstance(entry, dict):
        if entry.get("type") == "cloud":
            return (None, True)
        if entry.get("type") == "local":
            entry = entry.get("path")
    if not isinstance(entry, str):
        return (None, False)
    s = entry.strip()
    return (s if s else None, False)


def _add_local_path(ap, path_str, share_type, valid_paths, dynamic_folders):
    """Add a single local path (file or dir) to valid_paths/dynamic_folders."""
    if os.path.isfile(ap):
        valid_paths.append(path_str)
    elif os.path.isdir(ap):
        if share_type == "dynamic":
            dynamic_folders.append(path_str)
            logging.debug("Added dynamic folder: %s", path_str)
        else:
            try:
                all_files = get_all_files_recursive(ap, path_str)
                valid_paths.extend(all_files)
                logging.debug(
                    "Added %s files from directory: %s", len(all_files), path_str
                )
            except Exception:
                logging.exception("Error scanning directory %s", path_str)


def _collect_paths_from_request(paths, share_type, root_dir=None):
    """Parse paths from request; return (valid_paths, dynamic_folders, remote_items)."""
    if root_dir is None:
        root_dir = constants_module.ROOT_DIR
    valid_paths = []
    dynamic_folders = []
    remote_items = []
    for entry in paths:
        path_str, is_cloud = _normalize_path_entry(entry)
        if is_cloud:
            if isinstance(entry, dict):
                remote_items.append(entry)
            continue
        if not path_str:
            continue
        ap = os.path.abspath(os.path.join(root_dir, path_str))
        if not is_within_root(ap, root_dir):
            continue
        _add_local_path(ap, path_str, share_type, valid_paths, dynamic_folders)
    return valid_paths, dynamic_folders, remote_items


def _resolve_final_paths_dynamic(dynamic_folders, remote_items, sid):
    """Return (final_paths, error) for dynamic share. error is (status, body) or None."""
    if remote_items:
        remove_share_cloud_dir(sid)
        return (
            None,
            (400, {"error": "Cloud files are not supported in dynamic shares"}),
        )
    if not dynamic_folders:
        remove_share_cloud_dir(sid)
        return (None, (400, {"error": "No valid directories for dynamic share"}))
    return (dynamic_folders, None)


def _resolve_final_paths_static(valid_paths, remote_items, sid):
    """Return (final_paths, error) for static share. error is (status, body) or None."""
    combined_paths = list(valid_paths)
    if remote_items:
        try:
            cloud_paths = download_cloud_items(sid, remote_items)
            combined_paths.extend(cloud_paths)
        except CloudProviderError as cloud_error:
            remove_share_cloud_dir(sid)
            return (None, (400, {"error": str(cloud_error)}))
        except Exception:
            remove_share_cloud_dir(sid)
            logging.exception("Failed to download cloud files for share %s", sid)
            return (None, (500, {"error": CLOUD_DOWNLOAD_FAILED}))
    if not combined_paths:
        remove_share_cloud_dir(sid)
        return (None, (400, {"error": "No valid files or directories"}))
    seen_paths = set()
    final_paths = []
    for rel_path in combined_paths:
        if rel_path not in seen_paths:
            final_paths.append(rel_path)
            seen_paths.add(rel_path)
    return (final_paths, None)


def _resolve_share_paths(paths, share_type, sid, root_dir=None):
    valid_paths, dynamic_folders, remote_items = _collect_paths_from_request(
        paths, share_type, root_dir
    )
    if share_type == "dynamic":
        return _resolve_final_paths_dynamic(dynamic_folders, remote_items, sid)
    return _resolve_final_paths_static(valid_paths, remote_items, sid)


def _create_share_record(
    handler,
    db_conn,
    sid,
    final_paths,
    allowed_users,
    disable_token,
    share_type,
    allow_list,
    avoid_list,
    expiry_date,
    modify_users=None,
    tag_name=None,
    created_by=None,
):
    secret_token = secrets.token_urlsafe(64) if not disable_token else None
    created = datetime.now(timezone.utc).isoformat()
    success = handler.get_service("share_service").insert_share(
        db_conn,
        sid,
        created,
        final_paths,
        allowed_users if allowed_users else None,
        secret_token,
        share_type,
        allow_list if allow_list else None,
        avoid_list if avoid_list else None,
        expiry_date,
        modify_users=modify_users if modify_users else None,
        tag_name=tag_name,
        created_by=created_by,
    )
    return success, secret_token


# ---------------------------------------------------------------------------
# Helpers for share list/file access (token and user checks)
# ---------------------------------------------------------------------------


def _get_provided_token(share_id, request, get_cookie):
    """Extract token from Authorization header or cookie. Return None if missing."""
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        return auth_header[7:]
    return get_cookie(f"share_token_{share_id}")


def _share_access_without_secret_token(share: dict) -> tuple[bool, bool, tuple | None]:
    """When share has no secret_token: deny if user-restricted; else allow."""
    if share.get("allowed_users") or share.get("modify_users"):
        return False, False, (403, ACCESS_TOKEN_INVALID_OR_EXPIRED)
    return True, False, None


def _check_share_access(share, share_id, request, get_cookie, get_secure_cookie):
    """
    Check if the current request has access to the share based on user requirements.
    Returns (allowed: bool, redirect_to_verify: bool, error_tuple: tuple | None)
    """
    current_user = _get_cookie_username(get_secure_cookie)
    
    # 1. If user is logged in, check if they are the creator or explicitly allowed
    if current_user:
        creator = share.get("created_by") or share.get("creator") or ""
        if login_matches_share_creator_field(creator, current_user):
            return True, False, None
        allowed_users = share.get("allowed_users") or []
        if current_user in allowed_users:
            return True, False, None
        if current_user in (share.get("modify_users") or []):
            return True, False, None

    # 2. If NO token is enabled but allowed_users/modify_users restricts access,
    # require authenticated membership (already checked above — deny if we reach here).
    secret_token = share.get("secret_token")
    if not secret_token:
        return _share_access_without_secret_token(share)
    # 3. Restricted (token enabled) -> check if they provided a valid token
    provided = _get_provided_token(share_id, request, get_cookie)
    if provided and secrets.compare_digest(provided, secret_token):
        return True, False, None
        
    # Token required but missing or invalid
    return False, True, (403, ACCESS_TOKEN_INVALID_OR_EXPIRED)


def _get_cookie_username(get_secure_cookie):
    current_user = get_secure_cookie("user")
    if not current_user:
        return None
    raw = (
        current_user.decode("utf-8", errors="ignore")
        if isinstance(current_user, bytes)
        else str(current_user)
    )
    try:
        parsed = json.loads(raw)
        if isinstance(parsed, dict):
            return str(parsed.get("username", "")).strip() or None
        return str(parsed).strip() or None
    except Exception:
        return raw.strip() or None


def _root_dir_for_share(share: dict) -> str:
    """Filesystem root for paths on this share (creator home when multi-user)."""
    return filesystem_root_for_share(share)


def _is_user_allowed_for_modify(share, get_secure_cookie):
    """Return (True, None) if user can modify shared structure/files."""
    modify_users = share.get("modify_users") or []
    if not modify_users:
        return (False, (403, "This share is read-only"))
    username = _get_cookie_username(get_secure_cookie)
    if not username:
        return (False, (401, "Authentication required for modify access"))
    if username not in modify_users:
        return (False, (403, "Modify access denied for this share"))
    return (True, None)


def _get_share_file_list(share, db_conn=None):
    """Return list of file paths for the share (dynamic, static, or tag-based)."""
    share_type = share.get("share_type", "static")
    allow_list = share.get("allow_list", [])
    avoid_list = share.get("avoid_list", [])
    root = _root_dir_for_share(share)

    if share_type == "tag":
        return list_files_for_tag_share(
            db_conn, share.get("tag_name"), root, allow_list, avoid_list
        )

    if share_type == "dynamic":
        dynamic_files = []
        for folder_path in share.get("paths") or []:
            try:
                full_path = os.path.abspath(os.path.join(root, folder_path))
                if os.path.isdir(full_path) and is_within_root(full_path, root):
                    all_files = get_all_files_recursive(full_path, folder_path)
                    dynamic_files.extend(all_files)
            except Exception:
                logging.debug(
                    "Skipping dynamic share folder %r", folder_path, exc_info=True
                )
        return filter_files_by_patterns(dynamic_files, allow_list, avoid_list)

    return filter_files_by_patterns(share.get("paths") or [], allow_list, avoid_list)


def _is_path_in_share(share, path, db_conn=None):
    """Return True if path is allowed for this share (dynamic, static, or tag-based)."""
    root = _root_dir_for_share(share)
    return share_covers_relative_path(db_conn, share, path, root)


# ---------------------------------------------------------------------------
# Helpers for share update (path processing and token updates)
# ---------------------------------------------------------------------------


def _parse_path_entries_for_update(paths):
    """Parse path entries into (processed_paths, remote_items)."""
    remote_items = []
    processed_paths: list[str] = []
    for entry in paths:
        path_str, is_cloud = _normalize_path_entry(entry)
        if is_cloud and isinstance(entry, dict):
            remote_items.append(entry)
            continue
        if path_str:
            processed_paths.append(path_str)
    return processed_paths, remote_items


def _parse_paths_for_update(paths, share_id, requested_share_type, current_paths):
    """Parse paths list from update request. Return (deduped_paths, new_cloud_paths, cloud_to_remove, error)."""
    processed_paths, remote_items = _parse_path_entries_for_update(paths)

    if requested_share_type == "dynamic" and remote_items:
        return (
            None,
            [],
            [],
            (400, {"error": "Cloud files are not supported in dynamic shares"}),
        )

    new_cloud_paths: list[str] = []
    if remote_items:
        try:
            downloaded_paths = download_cloud_items(share_id, remote_items)
            processed_paths.extend(downloaded_paths)
            new_cloud_paths.extend(downloaded_paths)
        except CloudProviderError as cloud_error:
            return (None, [], [], (400, {"error": str(cloud_error)}))
        except Exception:
            logging.exception("Failed to download cloud files for share %s", share_id)
            return (None, [], [], (500, {"error": CLOUD_DOWNLOAD_FAILED}))

    seen_paths = set()
    deduped_paths = []
    for rel_path in processed_paths:
        if rel_path not in seen_paths:
            deduped_paths.append(rel_path)
            seen_paths.add(rel_path)
    removed_via_override = [
        p
        for p in current_paths
        if p not in deduped_paths and is_cloud_relative_path(share_id, p)
    ]
    return (deduped_paths, new_cloud_paths, removed_via_override, None)


def _validate_update_local_paths(handler, share_id, path_strings, share_type: str):
    """Ensure local path strings exist under the user's root. Return (status, body) or None."""
    root_dir = get_user_root(handler)
    for path_str in path_strings:
        if is_cloud_relative_path(share_id, path_str):
            continue
        ap = os.path.abspath(os.path.join(root_dir, path_str))
        if not is_within_root(ap, root_dir):
            return (400, {"error": f"Invalid path: {path_str}"})
        if share_type == "dynamic":
            if not os.path.isdir(ap):
                return (
                    400,
                    {"error": "Dynamic share paths must be existing directories"},
                )
        elif not (os.path.isfile(ap) or os.path.isdir(ap)):
            return (400, {"error": f"Path not found: {path_str}"})
    return None


def _build_token_update_fields(disable_token, share_data, rotate_existing=False):
    """Return dict of secret_token and disable_token for update_share."""
    if disable_token is True:
        return {"secret_token": None, "disable_token": True}
    if rotate_existing:
        return {"secret_token": secrets.token_urlsafe(64), "disable_token": False}
    if disable_token is False:
        st = share_data.get("secret_token")
        return {
            "secret_token": secrets.token_urlsafe(64) if st is None else st,
            "disable_token": False,
        }
    return {}


def _validate_share_update_request(handler, db_conn, data):
    """Validate update request and load share. Return (share_id, share_data) or (None, None, status, body)."""
    share_id = data.get("share_id")
    if not share_id:
        return (None, None, 400, {"error": "Share ID is required"})
    share_data = handler.get_service("share_service").get_share(db_conn, share_id)
    if not share_data:
        return (None, None, 404, {"error": "Share not found"})
    return (share_id, share_data, None, None)


def _build_share_update_response(updated_share, share_id, db_success, data, original_share=None):
    """Build JSON response dict for share update."""
    out = {"success": True, "share_id": share_id, "db_persisted": db_success}
    if "allowed_users" in data:
        out["allowed_users"] = updated_share.get("allowed_users")
    if "modify_users" in data:
        out["modify_users"] = updated_share.get("modify_users")
    if data.get("remove_files"):
        out["removed_files"] = data["remove_files"]
        out["remaining_files"] = updated_share.get("paths", [])
    if data.get("paths") is not None:
        out["updated_paths"] = updated_share.get("paths", [])
    if data.get("share_type") is not None:
        out["share_type"] = updated_share.get("share_type")
    if "expiry_date" in data:
        out["expiry_date"] = updated_share.get("expiry_date")
    if (
        (data.get("disable_token") is False or data.get("rotate_token"))
        and updated_share
        and updated_share.get("secret_token")
    ):
        # Only return new_token if it was actually changed/newly generated
        if not original_share or original_share.get("secret_token") != updated_share.get("secret_token"):
            out["new_token"] = updated_share.get("secret_token")
    return out


def _apply_remove_files(
    share_id, remove_files, current_paths, update_fields, cloud_paths_to_remove
):
    """Apply remove_files to update_fields and cloud_paths_to_remove."""
    if not remove_files:
        return
    update_fields["paths"] = [p for p in current_paths if p not in remove_files]
    cloud_paths_to_remove.extend(
        [p for p in remove_files if is_cloud_relative_path(share_id, p)]
    )
    logging.debug("Removing files %s from share %s", remove_files, share_id)


def _apply_paths_update(
    handler,
    share_id,
    paths,
    requested_share_type,
    current_paths,
    update_fields,
    cloud_paths_to_remove,
    new_cloud_paths,
):
    """Apply paths update. Return None on success, (status, body) on error."""
    if paths is None:
        return None
    deduped_paths, ncp, removed_cloud, err = _parse_paths_for_update(
        paths, share_id, requested_share_type, current_paths
    )
    if err is not None:
        return err
    if handler is not None:
        verr = _validate_update_local_paths(
            handler, share_id, deduped_paths, requested_share_type
        )
        if verr is not None:
            return verr
    update_fields["paths"] = deduped_paths
    cloud_paths_to_remove.extend(removed_cloud)
    new_cloud_paths.extend(ncp)
    logging.debug("Updating paths for share %s: %s", share_id, deduped_paths)
    return None


def _validate_share_type_change(requested_share_type: str, share_id: str, current_paths: list, data: dict) -> tuple | None:
    """Return error tuple or None if the type change is valid."""
    if requested_share_type == "tag" and (data.get("paths") is not None or data.get("remove_files")):
        return (400, {"error": "Tag shares do not use stored paths; change tag or filters elsewhere."})
    if requested_share_type == "dynamic" and any(is_cloud_relative_path(share_id, p) for p in current_paths):
        return (400, {"error": "Remove cloud files before switching to a dynamic share"})
    return None


def _apply_db_update(handler, db_conn, share_id, update_fields, new_cloud_paths) -> tuple | None:
    """Apply DB update; cleanup new cloud paths on failure. Returns error tuple or None."""
    if not update_fields:
        return None
    db_success = handler.get_service("share_service").update_share(db_conn, share_id, **update_fields)
    if not db_success:
        for rel_path in new_cloud_paths:
            remove_cloud_file_if_exists(share_id, rel_path)
        cleanup_share_cloud_dir_if_empty(share_id)
        return (500, {"error": "Failed to update share"})
    return None


def _execute_share_update(handler, db_conn, share_id, share_data, data):
    """Perform share update. Return (response_data, None, []) or (None, (status, body), new_cloud_paths)."""
    current_paths = share_data.get("paths", []) or []
    requested_share_type = (
        data.get("share_type") if data.get("share_type") is not None
        else share_data.get("share_type", "static")
    )
    type_err = _validate_share_type_change(requested_share_type, share_id, current_paths, data)
    if type_err is not None:
        return None, type_err, []

    update_fields, cloud_paths_to_remove, new_cloud_paths, err = _compute_share_update_fields(
        handler, share_id, share_data, data, current_paths, requested_share_type,
    )
    if err is not None:
        return (None, err, new_cloud_paths)

    db_err = _apply_db_update(handler, db_conn, share_id, update_fields, new_cloud_paths)
    if db_err is not None:
        return None, db_err, new_cloud_paths

    for rel_path in set(cloud_paths_to_remove):
        remove_cloud_file_if_exists(share_id, rel_path)
    cleanup_share_cloud_dir_if_empty(share_id)
    updated_share = handler.get_service("share_service").get_share(db_conn, share_id)
    return (
        _build_share_update_response(updated_share, share_id, bool(update_fields), data, share_data),
        None,
        [],
    )


def _apply_metadata_updates(data, share_data, update_fields):
    """Apply users/token/filters/expiry metadata to update_fields."""
    if "allowed_users" in data:
        update_fields["allowed_users"] = data.get("allowed_users") or None
    if "modify_users" in data:
        update_fields["modify_users"] = data.get("modify_users") or None
    dt = data.get("disable_token")
    rot = bool(data.get("rotate_token"))
    update_fields.update(_build_token_update_fields(dt, share_data, rotate_existing=rot))
    for key in ("allow_list", "avoid_list", "expiry_date"):
        if key in data:
            update_fields[key] = data.get(key)


def _compute_share_update_fields(
    handler,
    share_id,
    share_data,
    data,
    current_paths,
    requested_share_type,
):
    """Compute update_fields, cloud_paths_to_remove, new_cloud_paths. Return (update_fields, cloud_to_remove, new_cloud, error)."""
    update_fields = {}
    cloud_paths_to_remove = []
    new_cloud_paths = []
    if data.get("share_type") is not None:
        update_fields["share_type"] = data.get("share_type")
    _apply_remove_files(
        share_id,
        data.get("remove_files", []),
        current_paths,
        update_fields,
        cloud_paths_to_remove,
    )
    path_err = _apply_paths_update(
        handler,
        share_id,
        data.get("paths"),
        requested_share_type,
        current_paths,
        update_fields,
        cloud_paths_to_remove,
        new_cloud_paths,
    )
    if path_err is not None:
        return (None, [], [], path_err)
    _apply_metadata_updates(data, share_data, update_fields)
    return (update_fields, cloud_paths_to_remove, new_cloud_paths, None)


class ShareFilesHandler(BaseHandler):
    @tornado.web.authenticated
    @require_action("share.view")
    def get(self):
        if not self.require_feature(
            "file_share",
            True,
            body="Feature disabled: File sharing is currently disabled by administrator",
        ):
            return
        self.render("share.html", shares={})


class ShareCreateHandler(XSRFTokenMixin, BaseHandler):

    def _build_share_paths(self, req, sid: str) -> tuple[list | None, tuple | None]:
        """Return (final_paths, error_tuple) for share path resolution."""
        if req.share_type == "tag":
            if not req.tag_name:
                return None, (400, {"error": "tag_name is required for tag shares"})
            return [], None
        final_paths, err = _resolve_share_paths(
            req.paths, req.share_type, sid, root_dir=get_user_root(self)
        )
        return final_paths, err

    @tornado.web.authenticated
    @require_action("share.create")
    @require_db
    @require_modify_access()
    def post(self):
        if not self.require_feature(
            "file_share", True, body={"error": FS_DISABLED_MSG}
        ):
            return

        def action():
            data = self.parse_json_body(max_bytes=SHARE_JSON_BODY_MAX_BYTES) or {}
            vserr = validate_share_create_struct(data)
            if vserr:
                self.set_status(400)
                self.write({"error": vserr})
                return None
            req = ShareCreateRequest.from_payload(data)
            sid = secrets.token_urlsafe(64)

            final_paths, err = self._build_share_paths(req, sid)
            if err is not None:
                self.set_status(err[0])
                self.write(err[1])
                return None

            success, secret_token = _create_share_record(
                self, self.db_conn, sid, final_paths,
                req.allowed_users, req.disable_token, req.share_type,
                req.allow_list, req.avoid_list, req.expiry_date,
                modify_users=req.modify_users, tag_name=req.tag_name,
                created_by=get_username_string_for_db(self),
            )
            if not success:
                logging.error("Failed to create share %s in database", sid)
                self.write_json_error(500, "Failed to create share")
                return None

            label = req.tag_name if req.share_type == "tag" else f"paths={len(final_paths)}"
            logging.info("Share %s created successfully in database", sid)
            self.get_service("audit_service").log(
                self.db_conn, "share_create",
                username=self.get_display_username(),
                details=f"share_id={sid} {label}",
                ip=self.request.remote_ip,
            )
            self.publish_event(ShareCreatedEvent(
                share_id=sid, creator=self.get_display_username(),
                path_count=len(final_paths), created_at=now_ts(),
            ))
            response_data = ShareCreateResponse(
                share_id=sid, url=f"/shared/{sid}",
                secret_token=secret_token if not req.disable_token else None,
            )
            return response_data.to_json()

        self.run_json_action(action, on_error_message="Failed to create share. Please try again.")


class ShareRevokeHandler(XSRFTokenMixin, BaseHandler):
    @tornado.web.authenticated
    @require_action("share.revoke")
    @require_db
    @require_modify_access()
    def post(self):
        if not self.require_feature(
            "file_share", True, body={"error": FS_DISABLED_MSG}
        ):
            return
        sid = self.get_argument("id", "").strip()
        if not sid:
            self.set_status(400)
            self.write({"error": "Share id is required"})
            return
        if len(sid) > SHARE_ID_MAX_LEN:
            self.set_status(400)
            self.write({"error": "Invalid share id"})
            return
        try:
            share = self.get_service("share_service").get_share(self.db_conn, sid)
            if not share:
                self.set_status(404)
                self.write({"error": "Share not found"})
                return
            if not self.can_manage_share_secrets(share):
                self.set_status(403)
                self.write({"error": "Access denied"})
                return
            self.get_service("share_service").delete_share(self.db_conn, sid)
            self.get_service("audit_service").log(
                self.db_conn,
                "share_revoke",
                username=self.get_display_username(),
                details=f"share_id={sid}",
                ip=self.request.remote_ip,
            )
            logging.info(f"Share {sid} deleted from database")
        except Exception:
            logging.exception("Failed to delete share %s", sid)
            self.set_status(500)
            self.write({"error": "Failed to delete share"})
            return

        remove_share_cloud_dir(sid)

        if self.request.headers.get("Accept") == "application/json":
            self.write({"ok": True})
            return
        self.redirect("/share")


class ShareUpdateHandler(XSRFTokenMixin, BaseHandler):

    def _perform_share_update(self, data: dict) -> tuple:
        """Return (response_data, error_tuple, new_cloud_paths)."""
        vserr = validate_share_update_struct(data)
        if vserr:
            self.set_status(400)
            self.write({"error": vserr})
            return None, None, []
        share_id, share_data, err_status, err_body = _validate_share_update_request(self, self.db_conn, data)
        if err_status is not None:
            self.set_status(err_status)
            self.write(err_body)
            return None, None, []
        if not self.can_manage_share_secrets(share_data):
            self.set_status(403)
            self.write({"error": "Access denied"})
            return None, None, []
        response_data, err, new_cloud_paths = _execute_share_update(self, self.db_conn, share_id, share_data, data)
        if err is not None:
            self.set_status(err[0])
            self.write(err[1])
            return None, None, new_cloud_paths
        return response_data, share_id, new_cloud_paths

    @tornado.web.authenticated
    @require_db
    @require_modify_access()
    def post(self):
        """Update share access list"""
        if not self.require_feature(
            "file_share", True, body={"error": FS_DISABLED_MSG}
        ):
            return

        saved_share_id = None
        saved_new_cloud_paths: list[str] = []

        def action():
            nonlocal saved_share_id, saved_new_cloud_paths
            data = self.parse_json_body(max_bytes=SHARE_JSON_BODY_MAX_BYTES) or {}
            try:
                response_data, share_id, new_cloud_paths = self._perform_share_update(data)
                saved_share_id = share_id
                saved_new_cloud_paths = new_cloud_paths or []
                return response_data
            except Exception:
                if saved_share_id and saved_new_cloud_paths:
                    for rel_path in saved_new_cloud_paths:
                        remove_cloud_file_if_exists(saved_share_id, rel_path)
                    cleanup_share_cloud_dir_if_empty(saved_share_id)
                raise

        self.run_json_action(
            action,
            on_error_message="Failed to update share. Please try again.",
        )


class TokenVerificationHandler(BaseHandler):
    # Rate limiting for token verification (IP -> (attempts, timestamp))
    _TOKEN_VERIFY_ATTEMPTS = {}
    _RATE_LIMIT_WINDOW = 300  # 5 minutes
    _MAX_ATTEMPTS = 10  # Max 10 attempts per 5 minutes

    def check_xsrf_cookie(self):
        """Disable CSRF protection for token verification endpoint.
        This endpoint is meant to be accessed by external users without sessions.
        Rate limiting is used as an alternative protection measure."""
        pass

    def _check_rate_limit(self) -> bool:
        """Check if the request is within rate limits. Returns True if allowed."""
        remote_ip = self.request.remote_ip
        now = time.time()
        # Purge expired entries to prevent unbounded dict growth
        if len(self._TOKEN_VERIFY_ATTEMPTS) > 500:
            stale = [
                ip for ip, (_, ts) in self._TOKEN_VERIFY_ATTEMPTS.items()
                if now - ts > self._RATE_LIMIT_WINDOW
            ]
            for ip in stale:
                self._TOKEN_VERIFY_ATTEMPTS.pop(ip, None)
        attempts, timestamp = self._TOKEN_VERIFY_ATTEMPTS.get(remote_ip, (0, now))

        if now - timestamp > self._RATE_LIMIT_WINDOW:
            attempts = 0
            timestamp = now

        if attempts >= self._MAX_ATTEMPTS:
            return False

        self._TOKEN_VERIFY_ATTEMPTS[remote_ip] = (attempts + 1, timestamp)
        return True

    @require_db
    def get(self, sid):
        """Show token verification page — or redirect if no token is needed."""
        share = self.get_service("share_service").get_share(self.db_conn, sid)
        if not share:
            self.set_status(404)
            self.write(INVALID_SHARE_LINK)
            return
        if not share.get("secret_token"):
            self.redirect(f"/shared/{sid}")
            return
        allowed_ok, _, _ = _check_share_access(share, sid, self.request, self.get_cookie, self.get_secure_cookie)
        if allowed_ok:
            self.redirect(f"/shared/{sid}")
            return

        self.render("token_verification.html", share_id=sid)

    @require_db
    def post(self, sid):
        """Verify token and grant access"""
        # Rate limiting check
        if not self._check_rate_limit():
            self.write_json_error(429, "Too many attempts. Please try again later.")
            return

        share = self.get_service("share_service").get_share(self.db_conn, sid)
        if not share:
            self.write_json_error(404, "Invalid share link")
            return

        def action():
            data = self.parse_json_body() or {}
            provided_token = data.get("token", "").strip()
            if len(provided_token) > ACCESS_TOKEN_MAX_LEN:
                self.write_json_error(400, "Token is too long")
                return None
            stored_token = share.get("secret_token")

            if not stored_token:
                # Old share without secret token - allow access
                return {"success": True}

            if not provided_token:
                self.write_json_error(400, "Token is required")
                return None

            if not secrets.compare_digest(provided_token, stored_token):
                self.write_json_error(403, "Invalid token")
                return None

            self.set_cookie(
                f"share_token_{sid}",
                provided_token,
                path=f"/shared/{sid}",
                max_age=3600,
                httponly=True,
                secure=self.request.protocol == "https",
                samesite="Lax",
            )
            return {"success": True}

        self.run_json_action(action, on_error_message="Server error")


class SharedListHandler(BaseHandler):
    @require_db
    def get(self, sid):
        share = self.get_service("share_service").get_share(self.db_conn, sid)
        if not share:
            self.set_status(404)
            self.write(INVALID_SHARE_LINK)
            return
        if self.get_service("share_service").is_expired(share.get("expiry_date")):
            self.set_status(410)
            self.write("Share expired: This share is no longer available")
            return
        allowed_ok, redirect_to_verify, user_err = _check_share_access(share, sid, self.request, self.get_cookie, self.get_secure_cookie)
        if not allowed_ok:
            if redirect_to_verify:
                self.redirect(f"/shared/{sid}/verify")
            else:
                self.set_status(user_err[0])
                self.write(user_err[1])
            return
        filtered_files = _get_share_file_list(share, self.db_conn)
        can_modify, _ = _is_user_allowed_for_modify(share, self.get_secure_cookie)
        # Replace </script> so the raw JSON is safe to embed directly in a <script> tag
        files_json_safe = json.dumps(filtered_files).replace("</", "<\\/")
        self.render(
            "shared_list.html",
            share_id=sid,
            files=filtered_files,
            files_json=files_json_safe,
            can_modify=can_modify,
        )


class SharedFileHandler(BaseHandler):
    @require_db
    async def get(self, sid, path):
        share = self.get_service("share_service").get_share(self.db_conn, sid)
        if not share:
            self.set_status(404)
            self.write(INVALID_SHARE_LINK)
            return
        if self.get_service("share_service").is_expired(share.get("expiry_date")):
            self.set_status(410)
            self.write("Share expired: This share is no longer available")
            return
        allowed_ok, redirect_to_verify, user_err = _check_share_access(share, sid, self.request, self.get_cookie, self.get_secure_cookie)
        if not allowed_ok:
            if redirect_to_verify:
                self.set_status(403)
                self.write(ACCESS_TOKEN_INVALID_OR_EXPIRED)
            else:
                self.set_status(user_err[0])
                self.write(user_err[1])
            return
        if not _is_path_in_share(share, path, self.db_conn):
            self.set_status(403)
            self.write("Access denied: This file is not part of the share")
            return
        root = _root_dir_for_share(share)
        abspath = os.path.abspath(os.path.join(root, path))
        if not os.path.isfile(abspath):
            self.set_status(404)
            return
        # Track download for analytics
        if self.db_conn:
            try:
                self.get_service("audit_service").log(
                    self.db_conn,
                    "share_download",
                    details=f"share_id={sid} path={path}",
                    ip=self.request.remote_ip,
                )
            except Exception:
                logging.debug("share_download audit log failed", exc_info=True)
        await MainHandler.serve_file(self, abspath)
