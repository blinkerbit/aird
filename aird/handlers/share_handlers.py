import tornado.web
import json
import secrets
from datetime import datetime, timezone
import os
import logging

from aird.handlers.base_handler import BaseHandler
from aird.db import (
    insert_share,
    delete_share,
    get_all_shares,
    get_share_by_id,
    update_share,
    is_share_expired,
)
from aird.utils.util import (
    is_within_root,
    get_all_files_recursive,
    filter_files_by_patterns,
    is_cloud_relative_path,
    remove_cloud_file_if_exists,
    cleanup_share_cloud_dir_if_empty,
    remove_share_cloud_dir,
    download_cloud_items,
    is_feature_enabled,
)
from aird.config import ROOT_DIR
import aird.constants as constants_module
from aird.cloud import CloudProviderError


class ShareFilesHandler(BaseHandler):
    @tornado.web.authenticated
    def get(self):
        if not is_feature_enabled("file_share", True):
            self.set_status(403)
            self.write("Feature disabled: File sharing is currently disabled by administrator")
            return
        # Just render the template - files will be loaded on-the-fly via JavaScript
        # Pass empty dict since shares are fetched via API
        self.render("share.html", shares={})

class ShareCreateHandler(BaseHandler):
    def check_xsrf_cookie(self):
        """Override CSRF check to support X-XSRFToken header for JSON requests"""
        # Get token from cookie (expected value)
        cookie_token = self.get_cookie("_xsrf")
        if not cookie_token:
            raise tornado.web.HTTPError(403, "'_xsrf' argument missing from POST")
        
        # Get token from header or POST data
        provided_token = self.request.headers.get("X-XSRFToken")
        if not provided_token:
            # Fallback to POST argument for form submissions
            provided_token = self.get_argument("_xsrf", None)
        if not provided_token:
            raise tornado.web.HTTPError(403, "'_xsrf' argument missing from POST")
        
        # Compare tokens
        # Compare tokens using constant-time comparison
        if not secrets.compare_digest(provided_token, cookie_token):
            raise tornado.web.HTTPError(403, "XSRF cookie does not match POST argument")
    
    @tornado.web.authenticated
    def post(self):
        if not is_feature_enabled("file_share", True):
            self.set_status(403)
            self.write({"error": "File sharing is disabled"})
            return
        try:
            data = json.loads(self.request.body or b'{}')
            paths = data.get('paths', [])
            allowed_users = data.get('allowed_users', [])
            share_type = data.get('share_type', 'static')  # Default to static for backward compatibility
            allow_list = data.get('allow_list', [])
            avoid_list = data.get('avoid_list', [])
            disable_token = data.get('disable_token', False)
            expiry_date = data.get('expiry_date', None)
            
            valid_paths = []
            dynamic_folders = []  # Store folders for dynamic shares
            remote_items = []

            for entry in paths:
                candidate_path = entry
                if isinstance(entry, dict):
                    entry_type = entry.get('type')
                    if entry_type == 'cloud':
                        remote_items.append(entry)
                        continue
                    if entry_type == 'local':
                        candidate_path = entry.get('path')
                if not isinstance(candidate_path, str):
                    continue
                candidate_path = candidate_path.strip()
                if not candidate_path:
                    continue

                ap = os.path.abspath(os.path.join(ROOT_DIR, candidate_path))
                if not is_within_root(ap, ROOT_DIR):
                    continue

                if os.path.isfile(ap):
                    # It's a file, add it directly
                    valid_paths.append(candidate_path)
                elif os.path.isdir(ap):
                    if share_type == 'dynamic':
                        # For dynamic shares, store the folder path
                        dynamic_folders.append(candidate_path)
                        logging.debug(f"Added dynamic folder: {candidate_path}")
                    else:
                        # For static shares, recursively get all files
                        try:
                            all_files = get_all_files_recursive(ap, candidate_path)
                            valid_paths.extend(all_files)
                            logging.debug(f"Added {len(all_files)} files from directory: {candidate_path}")
                        except Exception as e:
                            logging.error(f"Error scanning directory {candidate_path}: {e}")
                            continue

            sid = secrets.token_urlsafe(64)  # Increase entropy to reduce guessing risk (Priority 2)

            if share_type == 'dynamic':
                if remote_items:
                    self.set_status(400)
                    self.write({"error": "Cloud files are not supported in dynamic shares"})
                    remove_share_cloud_dir(sid)
                    return
                if not dynamic_folders:
                    self.set_status(400)
                    self.write({"error": "No valid directories for dynamic share"})
                    remove_share_cloud_dir(sid)
                    return
                # For dynamic shares, store the folder paths
                final_paths = dynamic_folders
            else:
                combined_paths = list(valid_paths)
                if remote_items:
                    try:
                        cloud_paths = download_cloud_items(sid, remote_items)
                        combined_paths.extend(cloud_paths)
                    except CloudProviderError as cloud_error:
                        remove_share_cloud_dir(sid)
                        self.set_status(400)
                        self.write({"error": str(cloud_error)})
                        return
                    except Exception as exc:
                        remove_share_cloud_dir(sid)
                        logging.exception("Failed to download cloud files for share %s", sid)
                        self.set_status(500)
                        self.write({"error": "Failed to download cloud files"})
                        return

                if not combined_paths:
                    remove_share_cloud_dir(sid)
                    self.set_status(400)
                    self.write({"error": "No valid files or directories"})
                    return

                seen_paths = set()
                final_paths = []
                for rel_path in combined_paths:
                    if rel_path not in seen_paths:
                        final_paths.append(rel_path)
                        seen_paths.add(rel_path)

            secret_token = secrets.token_urlsafe(64) if not disable_token else None  # Generate secret token only if not disabled
            created = datetime.now(timezone.utc).isoformat()
            
            # Persist directly to database
            # Access DB_CONN from constants module to ensure we have the latest value
            db_conn = constants_module.DB_CONN
            if not db_conn:
                logging.error("Database connection not available. Cannot create share.")
                self.set_status(500)
                self.write({"error": "Database connection not available"})
                return
            success = insert_share(db_conn, sid, created, final_paths, allowed_users if allowed_users else None, secret_token, share_type, allow_list if allow_list else None, avoid_list if avoid_list else None, expiry_date)
            if success:
                logging.info(f"Share {sid} created successfully in database")
                response_data = {"id": sid, "url": f"/shared/{sid}"}
                if not disable_token:
                    response_data["secret_token"] = secret_token
                self.write(response_data)
            else:
                logging.error(f"Failed to create share {sid} in database")
                self.set_status(500)
                self.write({"error": "Failed to create share"})
        except Exception as e:
            logging.error(f"Share creation error: {e}")
            self.set_status(500)
            self.write({"error": "Failed to create share. Please try again."})

class ShareRevokeHandler(BaseHandler):
    @tornado.web.authenticated
    def post(self):
        if not is_feature_enabled("file_share", True):
            self.set_status(403)
            self.write({"error": "File sharing is disabled"})
            return
        sid = self.get_argument('id', '')
        
        # Delete from database
        try:
            db_conn = constants_module.DB_CONN
            if not db_conn:
                logging.error("Database connection not available. Cannot delete share.")
                self.set_status(500)
                self.write({"error": "Database connection not available"})
                return
            delete_share(db_conn, sid)
            logging.info(f"Share {sid} deleted from database")
        except Exception as e:
            logging.error(f"Failed to delete share {sid}: {e}")
            self.set_status(500)
            self.write({"error": f"Failed to delete share: {str(e)}"})
            
        if sid:
            remove_share_cloud_dir(sid)

        if self.request.headers.get('Accept') == 'application/json':
            self.write({'ok': True})
            return
        self.redirect('/share')

class ShareUpdateHandler(BaseHandler):
    def check_xsrf_cookie(self):
        """Override CSRF check to support X-XSRFToken header for JSON requests"""
        # Get token from cookie (expected value)
        cookie_token = self.get_cookie("_xsrf")
        if not cookie_token:
            raise tornado.web.HTTPError(403, "'_xsrf' argument missing from POST")
        
        # Get token from header or POST data
        provided_token = self.request.headers.get("X-XSRFToken")
        if not provided_token:
            # Fallback to POST argument for form submissions
            provided_token = self.get_argument("_xsrf", None)
        if not provided_token:
            raise tornado.web.HTTPError(403, "'_xsrf' argument missing from POST")
        
        # Compare tokens
        # Compare tokens using constant-time comparison
        if not secrets.compare_digest(provided_token, cookie_token):
            raise tornado.web.HTTPError(403, "XSRF cookie does not match POST argument")
    
    @tornado.web.authenticated
    def post(self):
        """Update share access list"""
        if not is_feature_enabled("file_share", True):
            self.set_status(403)
            self.write({"error": "File sharing is disabled"})
            return

        share_id = None
        new_cloud_paths: list[str] = []
        try:
            data = json.loads(self.request.body or b'{}')
            share_id = data.get('share_id')
            allowed_users = data.get('allowed_users')
            remove_files = data.get('remove_files', [])
            paths = data.get('paths')  # New: support for updating entire paths list
            disable_token = data.get('disable_token')
            allow_list = data.get('allow_list')
            avoid_list = data.get('avoid_list')
            share_type = data.get('share_type')
            expiry_date = data.get('expiry_date')

            if not share_id:
                self.set_status(400)
                self.write({"error": "Share ID is required"})
                return

            # Get current share data from database
            db_conn = constants_module.DB_CONN
            if not db_conn:
                logging.error("Database connection not available. Cannot update share.")
                self.set_status(500)
                self.write({"error": "Database connection not available"})
                return
            share_data = get_share_by_id(db_conn, share_id)
            if not share_data:
                self.set_status(404)
                self.write({"error": "Share not found"})
                return

            current_paths = share_data.get('paths', []) or []
            requested_share_type = share_type if share_type is not None else share_data.get('share_type', 'static')

            if requested_share_type == 'dynamic' and any(
                is_cloud_relative_path(share_id, path) for path in current_paths
            ):
                self.set_status(400)
                self.write({"error": "Remove cloud files before switching to a dynamic share"})
                return

            update_fields: dict[str, object] = {}
            cloud_paths_to_remove: list[str] = []

            if share_type is not None:
                update_fields['share_type'] = share_type

            # Handle file removal requests
            if remove_files:
                updated_paths = [p for p in current_paths if p not in remove_files]
                update_fields['paths'] = updated_paths
                cloud_paths_to_remove.extend(
                    [p for p in remove_files if is_cloud_relative_path(share_id, p)]
                )
                logging.debug(f"Removing files {remove_files} from share {share_id}")

            # Handle complete paths update (for adding files)
            if paths is not None:
                remote_items = []
                processed_paths: list[str] = []

                for entry in paths:
                    candidate_path = entry
                    if isinstance(entry, dict):
                        entry_type = entry.get('type')
                        if entry_type == 'cloud':
                            remote_items.append(entry)
                            continue
                        if entry_type == 'local':
                            candidate_path = entry.get('path')
                    if not isinstance(candidate_path, str):
                        continue
                    cleaned = candidate_path.strip()
                    if not cleaned:
                        continue
                    processed_paths.append(cleaned)

                if requested_share_type == 'dynamic' and remote_items:
                    self.set_status(400)
                    self.write({"error": "Cloud files are not supported in dynamic shares"})
                    return

                if remote_items:
                    try:
                        downloaded_paths = download_cloud_items(share_id, remote_items)
                        processed_paths.extend(downloaded_paths)
                        new_cloud_paths.extend(downloaded_paths)
                    except CloudProviderError as cloud_error:
                        self.set_status(400)
                        self.write({"error": str(cloud_error)})
                        return
                    except Exception:
                        logging.exception("Failed to download cloud files for share %s", share_id)
                        self.set_status(500)
                        self.write({"error": "Failed to download cloud files"})
                        return

                seen_paths = set()
                deduped_paths: list[str] = []
                for rel_path in processed_paths:
                    if rel_path not in seen_paths:
                        deduped_paths.append(rel_path)
                        seen_paths.add(rel_path)

                update_fields['paths'] = deduped_paths
                removed_via_override = [
                    p for p in current_paths
                    if p not in deduped_paths and is_cloud_relative_path(share_id, p)
                ]
                cloud_paths_to_remove.extend(removed_via_override)
                logging.debug(f"Updating paths for share {share_id}: {deduped_paths}")

            # Update allowed users if provided
            if allowed_users is not None:
                update_fields['allowed_users'] = allowed_users if allowed_users else None

            if disable_token is True:
                update_fields['secret_token'] = None
                update_fields['disable_token'] = True
            elif disable_token is False:
                if share_data['secret_token'] is None:
                    update_fields['secret_token'] = secrets.token_urlsafe(64)
                else:
                    update_fields['secret_token'] = share_data['secret_token']
                update_fields['disable_token'] = False

            if allow_list is not None:
                update_fields['allow_list'] = allow_list
            if avoid_list is not None:
                update_fields['avoid_list'] = avoid_list
            if expiry_date is not None:
                update_fields['expiry_date'] = expiry_date

            # Persist updates
            if update_fields:
                db_success = update_share(db_conn, share_id, **update_fields)
                if not db_success:
                    for rel_path in new_cloud_paths:
                        remove_cloud_file_if_exists(share_id, rel_path)
                    cleanup_share_cloud_dir_if_empty(share_id)
                    self.set_status(500)
                    self.write({"error": "Failed to update share"})
                    return
            else:
                db_success = True  # No updates needed

            if cloud_paths_to_remove:
                for rel_path in set(cloud_paths_to_remove):
                    remove_cloud_file_if_exists(share_id, rel_path)
            cleanup_share_cloud_dir_if_empty(share_id)

            # Get updated share data for response
            updated_share = get_share_by_id(db_conn, share_id)

            response_data = {
                "success": True,
                "share_id": share_id,
                "db_persisted": db_success
            }

            if allowed_users is not None:
                response_data["allowed_users"] = updated_share.get('allowed_users')
            if remove_files:
                response_data["removed_files"] = remove_files
                response_data["remaining_files"] = updated_share.get('paths', [])
            if paths is not None:
                response_data["updated_paths"] = updated_share.get('paths', [])
            if share_type is not None:
                response_data["share_type"] = updated_share.get('share_type')
            if expiry_date is not None:
                response_data["expiry_date"] = updated_share.get('expiry_date')
            if disable_token is False and updated_share and updated_share.get('secret_token'):
                response_data["new_token"] = updated_share.get('secret_token')

            self.write(response_data)

        except Exception as e:
            logging.error(f"Share update error: {e}")
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
        import time
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
    
    def get(self, sid):
        """Show token verification page"""
        db_conn = constants_module.DB_CONN
        if not db_conn:
            self.set_status(500)
            self.write("Database connection not available")
            return
        share = get_share_by_id(db_conn, sid)
        if not share:
            self.set_status(404)
            self.write("Invalid share link: The requested share does not exist or has been removed")
            return
        
        self.render("token_verification.html", share_id=sid)

    def post(self, sid):
        """Verify token and grant access"""
        # Rate limiting check
        if not self._check_rate_limit():
            self.set_status(429)
            self.write({"error": "Too many attempts. Please try again later."})
            return
        
        db_conn = constants_module.DB_CONN
        if not db_conn:
            self.set_status(500)
            self.write("Database connection not available")
            return
        share = get_share_by_id(db_conn, sid)
        if not share:
            self.set_status(404)
            self.write({"error": "Invalid share link"})
            return
        
        try:
            data = json.loads(self.request.body or b'{}')
            provided_token = data.get('token', '').strip()
            stored_token = share.get('secret_token')
            
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
            
        except Exception as e:
            self.set_status(500)
            self.write({"error": "Server error"})

class SharedListHandler(BaseHandler):
    def get(self, sid):
        db_conn = constants_module.DB_CONN
        if not db_conn:
            self.set_status(500)
            self.write("Database connection not available")
            return
        share = get_share_by_id(db_conn, sid)
        if not share:
            self.set_status(404)
            self.write("Invalid share link: The requested share does not exist or has been removed")
            return
        
        # Check if share has expired
        expiry_date = share.get('expiry_date')
        if is_share_expired(expiry_date):
            self.set_status(410)  # Gone
            self.write("Share expired: This share is no longer available")
            return
        
        # Check if share requires token verification
        secret_token = share.get('secret_token')
        if secret_token:
            # Check for Authorization header first
            auth_header = self.request.headers.get('Authorization', '')
            provided_token = None
            
            if auth_header.startswith('Bearer '):
                provided_token = auth_header[7:]  # Remove 'Bearer ' prefix
            else:
                # Check for cookie as fallback
                cookie_name = f"share_token_{sid}"
                provided_token = self.get_cookie(cookie_name)
            
            if not provided_token or not secrets.compare_digest(provided_token, secret_token):
                # No valid token found, redirect to verification
                self.redirect(f"/shared/{sid}/verify")
                return
        # If secret_token is None or empty, no token verification is required
        
        # Check if share has user restrictions
        allowed_users = share.get('allowed_users')
        if allowed_users:
            # Get current user from cookie
            current_user = self.get_secure_cookie("user")
            if not current_user:
                self.set_status(401)
                self.write("Authentication required: Please provide a valid access token")
                return
            
            # Decode username if it's bytes
            if isinstance(current_user, bytes):
                current_user = current_user.decode('utf-8')
            
            # Check if current user is in allowed users list
            if current_user not in allowed_users:
                self.set_status(403)
                self.write("Access denied: Invalid or expired access token")
                return
        
        # Handle dynamic vs static shares
        share_type = share.get('share_type', 'static')
        allow_list = share.get('allow_list', [])
        avoid_list = share.get('avoid_list', [])
        
        if share_type == 'dynamic':
            # For dynamic shares, scan the folders in real-time
            dynamic_files = []
            for folder_path in share['paths']:
                try:
                    full_path = os.path.abspath(os.path.join(ROOT_DIR, folder_path))
                    if os.path.isdir(full_path) and is_within_root(full_path, ROOT_DIR):
                        # Recursively scan the folder for current files
                        all_files = get_all_files_recursive(full_path, folder_path)
                        dynamic_files.extend(all_files)
                except Exception as e:
                    continue
            
            # Apply allow/avoid list filtering
            filtered_files = filter_files_by_patterns(dynamic_files, allow_list, avoid_list)
            self.render("shared_list.html", share_id=sid, files=filtered_files, files_json=json.dumps(filtered_files))
        else:
            # For static shares, use the stored paths
            
            # Apply allow/avoid list filtering to static shares
            filtered_files = filter_files_by_patterns(share['paths'], allow_list, avoid_list)
            self.render("shared_list.html", share_id=sid, files=filtered_files, files_json=json.dumps(filtered_files))

class SharedFileHandler(BaseHandler):
    async def get(self, sid, path):
        db_conn = constants_module.DB_CONN
        if not db_conn:
            self.set_status(500)
            self.write("Database connection not available")
            return
        share = get_share_by_id(db_conn, sid)
        if not share:
            self.set_status(404)
            self.write("Invalid share link: The requested share does not exist or has been removed")
            return
        
        # Check if share has expired
        expiry_date = share.get('expiry_date')
        if is_share_expired(expiry_date):
            self.set_status(410)  # Gone
            self.write("Share expired: This share is no longer available")
            return
        
        # Check if share requires token verification
        secret_token = share.get('secret_token')
        if secret_token:
            # Check for Authorization header first
            auth_header = self.request.headers.get('Authorization', '')
            provided_token = None
            
            if auth_header.startswith('Bearer '):
                provided_token = auth_header[7:]
            else:
                # Check for cookie as fallback
                cookie_name = f"share_token_{sid}"
                provided_token = self.get_cookie(cookie_name)
            
            if not provided_token or not secrets.compare_digest(provided_token, secret_token):
                self.set_status(403)
                self.write("Access denied: Invalid or expired access token")
                return
        
        # Check if share has user restrictions
        allowed_users = share.get('allowed_users')
        if allowed_users:
            current_user = self.get_secure_cookie("user")
            if not current_user:
                self.set_status(401)
                self.write("Authentication required: Please provide a valid access token")
                return
            
            if isinstance(current_user, bytes):
                current_user = current_user.decode('utf-8')
            
            if current_user not in allowed_users:
                self.set_status(403)
                self.write("Access denied: Invalid or expired access token")
                return
        
        share_type = share.get('share_type', 'static')
        allow_list = share.get('allow_list', [])
        avoid_list = share.get('avoid_list', [])

        if share_type == 'dynamic':
            # For dynamic shares, we need to verify the path is within one of the shared folders
            is_authorized = False
            for folder_path in share['paths']:
                try:
                    full_folder_path = os.path.abspath(os.path.join(ROOT_DIR, folder_path))
                    full_file_path = os.path.abspath(os.path.join(ROOT_DIR, path))
                    if os.path.isdir(full_folder_path) and is_within_root(full_file_path, full_folder_path):
                        # Path is within a shared dynamic folder, now check allow/avoid lists
                        if not filter_files_by_patterns([path], allow_list, avoid_list):
                            continue # This specific file is filtered out
                        is_authorized = True
                        break
                except Exception:
                    continue
            
            if not is_authorized:
                self.set_status(403)
                self.write("Access denied: This file is not part of the share")
                return
        else:
            # For static shares, check if the path is in the stored list after filtering
            filtered_paths = filter_files_by_patterns(share['paths'], allow_list, avoid_list)
            if path not in filtered_paths:
                self.set_status(403)
                self.write("Access denied: This file is not part of the share")
                return
        
        abspath = os.path.abspath(os.path.join(ROOT_DIR, path))
        if not os.path.isfile(abspath):
            self.set_status(404)
            return

        await FileHandler.serve_file(self, abspath)
