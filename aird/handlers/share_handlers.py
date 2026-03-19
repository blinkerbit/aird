import tornado.web
import json
import secrets
import time
from datetime import datetime, timezone
import os
import logging

from aird.handlers.base_handler import BaseHandler, XSRFTokenMixin, require_db
from aird.db import (
    insert_share,
    delete_share,
    get_share_by_id,
    update_share,
    is_share_expired,
    log_audit,
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
)
from aird.config import ROOT_DIR
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
            except Exception as e:
                logging.error("Error scanning directory %s: %s", path_str, e)


def _collect_paths_from_request(paths, share_type):
    """Parse paths from request; return (valid_paths, dynamic_folders, remote_items)."""
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
        ap = os.path.abspath(os.path.join(ROOT_DIR, path_str))
        if not is_within_root(ap, ROOT_DIR):
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
            return (None, (500, {"error": "Failed to download cloud files"}))
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


# ---------------------------------------------------------------------------
# Helpers for share list/file access (token and user checks)
# ---------------------------------------------------------------------------


def _get_provided_token(share_id, request, get_cookie):
    """Extract token from Authorization header or cookie. Return None if missing."""
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        return auth_header[7:]
    return get_cookie(f"share_token_{share_id}")


def _is_token_valid(share, share_id, request, get_cookie):
    """Return True if share has no token or provided token matches."""
    secret_token = share.get("secret_token")
    if not secret_token:
        return True
    provided = _get_provided_token(share_id, request, get_cookie)
    return bool(provided and secrets.compare_digest(provided, secret_token))


def _is_user_allowed(share, get_secure_cookie):
    """Return (True, None) if allowed, else (False, (status, body))."""
    allowed_users = share.get("allowed_users")
    if not allowed_users:
        return (True, None)
    current_user = get_secure_cookie("user")
    if not current_user:
        return (
            False,
            (401, "Authentication required: Please provide a valid access token"),
        )
    if isinstance(current_user, bytes):
        current_user = current_user.decode("utf-8")
    if current_user not in allowed_users:
        return (False, (403, ACCESS_TOKEN_INVALID_OR_EXPIRED))
    return (True, None)


def _get_share_file_list(share):
    """Return list of file paths for the share (dynamic or static)."""
    share_type = share.get("share_type", "static")
    allow_list = share.get("allow_list", [])
    avoid_list = share.get("avoid_list", [])
    if share_type == "dynamic":
        dynamic_files = []
        for folder_path in share["paths"]:
            try:
                full_path = os.path.abspath(os.path.join(ROOT_DIR, folder_path))
                if os.path.isdir(full_path) and is_within_root(full_path, ROOT_DIR):
                    all_files = get_all_files_recursive(full_path, folder_path)
                    dynamic_files.extend(all_files)
            except Exception:
                continue
        return filter_files_by_patterns(dynamic_files, allow_list, avoid_list)
    return filter_files_by_patterns(share["paths"], allow_list, avoid_list)


def _is_path_in_share(share, path):
    """Return True if path is allowed for this share (dynamic or static)."""
    share_type = share.get("share_type", "static")
    allow_list = share.get("allow_list", [])
    avoid_list = share.get("avoid_list", [])
    if share_type == "dynamic":
        for folder_path in share["paths"]:
            try:
                full_folder_path = os.path.abspath(os.path.join(ROOT_DIR, folder_path))
                full_file_path = os.path.abspath(os.path.join(ROOT_DIR, path))
                if (
                    os.path.isdir(full_folder_path)
                    and is_within_root(full_file_path, full_folder_path)
                    and filter_files_by_patterns([path], allow_list, avoid_list)
                ):
                    return True
            except Exception:
                continue
        return False
    filtered_paths = filter_files_by_patterns(share["paths"], allow_list, avoid_list)
    return path in filtered_paths


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
            return (None, [], [], (500, {"error": "Failed to download cloud files"}))

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


def _build_token_update_fields(disable_token, share_data):
    """Return dict of secret_token and disable_token for update_share."""
    if disable_token is True:
        return {"secret_token": None, "disable_token": True}
    if disable_token is False:
        st = share_data.get("secret_token")
        return {
            "secret_token": secrets.token_urlsafe(64) if st is None else st,
            "disable_token": False,
        }
    return {}


def _validate_share_update_request(db_conn, data):
    """Validate update request and load share. Return (share_id, share_data) or (None, None, status, body)."""
    share_id = data.get("share_id")
    if not share_id:
        return (None, None, 400, {"error": "Share ID is required"})
    share_data = get_share_by_id(db_conn, share_id)
    if not share_data:
        return (None, None, 404, {"error": "Share not found"})
    return (share_id, share_data, None, None)


def _build_share_update_response(updated_share, share_id, db_success, data):
    """Build JSON response dict for share update."""
    out = {"success": True, "share_id": share_id, "db_persisted": db_success}
    if data.get("allowed_users") is not None:
        out["allowed_users"] = updated_share.get("allowed_users")
    if data.get("remove_files"):
        out["removed_files"] = data["remove_files"]
        out["remaining_files"] = updated_share.get("paths", [])
    if data.get("paths") is not None:
        out["updated_paths"] = updated_share.get("paths", [])
    if data.get("share_type") is not None:
        out["share_type"] = updated_share.get("share_type")
    if data.get("expiry_date") is not None:
        out["expiry_date"] = updated_share.get("expiry_date")
    if (
        data.get("disable_token") is False
        and updated_share
        and updated_share.get("secret_token")
    ):
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
    update_fields["paths"] = deduped_paths
    cloud_paths_to_remove.extend(removed_cloud)
    new_cloud_paths.extend(ncp)
    logging.debug("Updating paths for share %s: %s", share_id, deduped_paths)
    return None


def _execute_share_update(db_conn, share_id, share_data, data):
    """Perform share update. Return (response_data, None, []) or (None, (status, body), new_cloud_paths)."""
    current_paths = share_data.get("paths", []) or []
    requested_share_type = (
        data.get("share_type")
        if data.get("share_type") is not None
        else share_data.get("share_type", "static")
    )
    if requested_share_type == "dynamic" and any(
        is_cloud_relative_path(share_id, p) for p in current_paths
    ):
        return (
            None,
            (400, {"error": "Remove cloud files before switching to a dynamic share"}),
            [],
        )

    update_fields, cloud_paths_to_remove, new_cloud_paths, err = (
        _compute_share_update_fields(
            share_id, share_data, data, current_paths, requested_share_type
        )
    )
    if err is not None:
        return (None, err, new_cloud_paths)
    if update_fields:
        db_success = update_share(db_conn, share_id, **update_fields)
        if not db_success:
            for rel_path in new_cloud_paths:
                remove_cloud_file_if_exists(share_id, rel_path)
            cleanup_share_cloud_dir_if_empty(share_id)
            return (None, (500, {"error": "Failed to update share"}), new_cloud_paths)
    else:
        db_success = True
    for rel_path in set(cloud_paths_to_remove):
        remove_cloud_file_if_exists(share_id, rel_path)
    cleanup_share_cloud_dir_if_empty(share_id)
    updated_share = get_share_by_id(db_conn, share_id)
    return (
        _build_share_update_response(updated_share, share_id, db_success, data),
        None,
        [],
    )


def _apply_metadata_updates(data, share_data, update_fields):
    """Apply allowed_users, token, allow_list, avoid_list, expiry_date to update_fields."""
    if data.get("allowed_users") is not None:
        update_fields["allowed_users"] = data.get("allowed_users") or None
    update_fields.update(
        _build_token_update_fields(data.get("disable_token"), share_data)
    )
    for key in ("allow_list", "avoid_list", "expiry_date"):
        if data.get(key) is not None:
            update_fields[key] = data.get(key)


def _compute_share_update_fields(
    share_id, share_data, data, current_paths, requested_share_type
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
    def get(self):
        if not self.require_feature(
            "file_share",
            True,
            body="Feature disabled: File sharing is currently disabled by administrator",
        ):
            return
        self.render("share.html", shares={})


class ShareCreateHandler(XSRFTokenMixin, BaseHandler):

    @tornado.web.authenticated
    @require_db
    def post(self):
        if not self.require_feature(
            "file_share", True, body={"error": FS_DISABLED_MSG}
        ):
            return
        try:
            data = self.parse_json_body()
            paths = data.get("paths", [])
            allowed_users = data.get("allowed_users", [])
            share_type = data.get("share_type", "static")
            allow_list = data.get("allow_list", [])
            avoid_list = data.get("avoid_list", [])
            disable_token = data.get("disable_token", False)
            expiry_date = data.get("expiry_date", None)

            valid_paths, dynamic_folders, remote_items = _collect_paths_from_request(
                paths, share_type
            )
            sid = secrets.token_urlsafe(64)

            if share_type == "dynamic":
                final_paths, err = _resolve_final_paths_dynamic(
                    dynamic_folders, remote_items, sid
                )
            else:
                final_paths, err = _resolve_final_paths_static(
                    valid_paths, remote_items, sid
                )

            if err is not None:
                status, body = err
                self.set_status(status)
                self.write(body)
                return

            secret_token = secrets.token_urlsafe(64) if not disable_token else None
            created = datetime.now(timezone.utc).isoformat()
            success = insert_share(
                self.db_conn,
                sid,
                created,
                final_paths,
                allowed_users if allowed_users else None,
                secret_token,
                share_type,
                allow_list if allow_list else None,
                avoid_list if avoid_list else None,
                expiry_date,
            )
            if success:
                logging.info("Share %s created successfully in database", sid)
                log_audit(
                    self.db_conn,
                    "share_create",
                    username=self.get_display_username(),
                    details=f"share_id={sid} paths={len(final_paths)}",
                    ip=self.request.remote_ip,
                )
                response_data = {"id": sid, "url": f"/shared/{sid}"}
                if not disable_token:
                    response_data["secret_token"] = secret_token
                self.write(response_data)
            else:
                logging.error("Failed to create share %s in database", sid)
                self.set_status(500)
                self.write({"error": "Failed to create share"})
        except Exception as e:
            logging.error("Share creation error: %s", e)
            self.set_status(500)
            self.write({"error": "Failed to create share. Please try again."})


class ShareRevokeHandler(XSRFTokenMixin, BaseHandler):
    @tornado.web.authenticated
    @require_db
    def post(self):
        if not self.require_feature(
            "file_share", True, body={"error": FS_DISABLED_MSG}
        ):
            return
        sid = self.get_argument("id", "")

        # Delete from database
        try:
            delete_share(self.db_conn, sid)
            log_audit(
                self.db_conn,
                "share_revoke",
                username=self.get_display_username(),
                details=f"share_id={sid}",
                ip=self.request.remote_ip,
            )
            logging.info(f"Share {sid} deleted from database")
        except Exception as e:
            logging.error(f"Failed to delete share {sid}: {e}")
            self.set_status(500)
            self.write({"error": "Failed to delete share"})

        if sid:
            remove_share_cloud_dir(sid)

        if self.request.headers.get("Accept") == "application/json":
            self.write({"ok": True})
            return
        self.redirect("/share")


class ShareUpdateHandler(XSRFTokenMixin, BaseHandler):

    @tornado.web.authenticated
    @require_db
    def post(self):
        """Update share access list"""
        if not self.require_feature(
            "file_share", True, body={"error": FS_DISABLED_MSG}
        ):
            return

        share_id = None
        new_cloud_paths: list[str] = []
        try:
            data = self.parse_json_body()
            share_id, share_data, err_status, err_body = _validate_share_update_request(
                self.db_conn, data
            )
            if err_status is not None:
                self.set_status(err_status)
                self.write(err_body)
                return

            response_data, err, new_cloud_paths = _execute_share_update(
                self.db_conn, share_id, share_data, data
            )
            if err is not None:
                self.set_status(err[0])
                self.write(err[1])
                return
            self.write(response_data)

        except Exception as e:
            logging.error("Share update error: %s", e)
            if share_id and new_cloud_paths:
                for rel_path in new_cloud_paths:
                    remove_cloud_file_if_exists(share_id, rel_path)
                cleanup_share_cloud_dir_if_empty(share_id)
            self.set_status(500)
            self.write({"error": "Failed to update share. Please try again."})


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
        """Show token verification page"""
        share = get_share_by_id(self.db_conn, sid)
        if not share:
            self.set_status(404)
            self.write(INVALID_SHARE_LINK)
            return

        self.render("token_verification.html", share_id=sid)

    @require_db
    def post(self, sid):
        """Verify token and grant access"""
        # Rate limiting check
        if not self._check_rate_limit():
            self.set_status(429)
            self.write({"error": "Too many attempts. Please try again later."})
            return

        share = get_share_by_id(self.db_conn, sid)
        if not share:
            self.set_status(404)
            self.write({"error": "Invalid share link"})
            return

        try:
            data = self.parse_json_body()
            provided_token = data.get("token", "").strip()
            stored_token = share.get("secret_token")

            if not stored_token:
                # Old share without secret token - allow access
                self.write({"success": True})
                return

            if not provided_token:
                self.set_status(400)
                self.write({"error": "Token is required"})
                return

            if not secrets.compare_digest(provided_token, stored_token):
                self.set_status(403)
                self.write({"error": "Invalid token"})
                return

            # Token is valid
            self.write({"success": True})

        except Exception:
            self.set_status(500)
            self.write({"error": "Server error"})


class SharedListHandler(BaseHandler):
    @require_db
    def get(self, sid):
        share = get_share_by_id(self.db_conn, sid)
        if not share:
            self.set_status(404)
            self.write(INVALID_SHARE_LINK)
            return
        if is_share_expired(share.get("expiry_date")):
            self.set_status(410)
            self.write("Share expired: This share is no longer available")
            return
        if not _is_token_valid(share, sid, self.request, self.get_cookie):
            self.redirect(f"/shared/{sid}/verify")
            return
        allowed_ok, user_err = _is_user_allowed(share, self.get_secure_cookie)
        if not allowed_ok:
            self.set_status(user_err[0])
            self.write(user_err[1])
            return
        filtered_files = _get_share_file_list(share)
        self.render(
            "shared_list.html",
            share_id=sid,
            files=filtered_files,
            files_json=json.dumps(filtered_files),
        )


class SharedFileHandler(BaseHandler):
    @require_db
    async def get(self, sid, path):
        share = get_share_by_id(self.db_conn, sid)
        if not share:
            self.set_status(404)
            self.write(INVALID_SHARE_LINK)
            return
        if is_share_expired(share.get("expiry_date")):
            self.set_status(410)
            self.write("Share expired: This share is no longer available")
            return
        if not _is_token_valid(share, sid, self.request, self.get_cookie):
            self.set_status(403)
            self.write(ACCESS_TOKEN_INVALID_OR_EXPIRED)
            return
        allowed_ok, user_err = _is_user_allowed(share, self.get_secure_cookie)
        if not allowed_ok:
            self.set_status(user_err[0])
            self.write(user_err[1])
            return
        if not _is_path_in_share(share, path):
            self.set_status(403)
            self.write("Access denied: This file is not part of the share")
            return
        abspath = os.path.abspath(os.path.join(ROOT_DIR, path))
        if not os.path.isfile(abspath):
            self.set_status(404)
            return
        # Track download for analytics
        if self.db_conn:
            try:
                log_audit(
                    self.db_conn,
                    "share_download",
                    details=f"share_id={sid} path={path}",
                    ip=self.request.remote_ip,
                )
            except Exception:
                pass
        await MainHandler.serve_file(self, abspath)
