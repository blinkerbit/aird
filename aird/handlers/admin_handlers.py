import csv
import io
import logging
import os
import re
import secrets as _secrets
import socket as _socket
import time

import json
import tornado.web
from typing import Any

from aird.handlers.base_handler import BaseHandler, XSRFTokenMixin
from aird.handlers.api_handlers import (
    FeatureFlagSocketHandler,
    FileStreamHandler,
    RuntimeConfigSocketHandler,
    SuperSearchWebSocketHandler,
)
from aird.database.ldap import (
    create_ldap_config,
    delete_ldap_config,
    get_all_ldap_configs,
    get_ldap_config_by_id,
    get_ldap_sync_logs,
    update_ldap_config,
)
from aird.constants import (
    FEATURE_FLAGS,
    WEBSOCKET_CONFIG,
    UPLOAD_CONFIG,
    TRANSFER_CONFIG,
    COMPRESSION_CONFIG,
)
from aird.utils.util import invalidate_feature_flags_cache, invalidate_websocket_config_cache
from aird.network_share_manager import (
    is_smb_server_available,
    is_webdav_server_available,
    smb_library_available,
    webdav_library_available,
)
from aird.plugins.chat import chat_library_available
from aird.plugins.gitlab import gitlab_library_available
from aird.constants.admin import (
    ACCESS_DENIED,
    ACCESS_DENIED_JSON,
    ALL_FIELDS_REQUIRED,
    CONFIG_NAME_LENGTH,
    CONFIG_NOT_FOUND,
    CONTENT_TYPE_CSV,
    CONTENT_TYPE_JSON,
    DATABASE_NOT_AVAILABLE,
    ERROR_UPDATE_CONFIG,
    ERROR_UPDATE_USER,
    FAILED_CREATE_USER,
    FAILED_UPDATE_USER,
    HTTP_BAD_REQUEST,
    HTTP_FORBIDDEN,
    INVALID_CONFIG_ID,
    INVALID_ROLE,
    INVALID_USER_ID,
    INVALID_USER_ID_SHORT,
    LDAP_PASSWORD_CHANGE,
    SYNC_STARTED,
    URL_ADMIN,
    URL_ADMIN_LDAP,
    URL_ADMIN_LOGIN,
    ERR_ALL_FIELDS_REQUIRED,
    ERR_DB_UNAVAILABLE,
    ERR_FAILED_CREATE_SHARE,
    ERR_FOLDER_NOT_EXIST,
    ERR_INVALID_PROTOCOL,
    ERR_SMB_UNAVAILABLE,
    ERR_WEBDAV_UNAVAILABLE,
    ERR_PORT_RANGE,
    URL_ADMIN_NETWORK_SHARES,
    URL_ADMIN_PATHS,
    URL_ADMIN_PLUGINS,
    URL_ADMIN_USERS,
    USERNAME_LENGTH,
    USERNAME_PASSWORD_REQUIRED,
    USERNAME_REQUIRED,
    TEMPLATE_LDAP_CONFIG_CREATE,
    TEMPLATE_LDAP_CONFIG_EDIT,
    TEMPLATE_USER_CREATE,
    TEMPLATE_USER_EDIT,
    USER_NOT_FOUND,
)
import aird.constants as constants_module
from aird.utils.util import get_current_feature_flags, get_current_websocket_config
from aird.core.security import validate_password
from aird.handlers.base_handler import require_admin, require_db


def _get_user_service(handler) -> Any:
    return handler.get_service("user_service")


_VALID_TRANSFER_PROFILES = ("cloudflare", "wireguard", "open")

_ADMIN_FEATURE_FLAG_NAMES = (
    "file_upload",
    "file_delete",
    "file_rename",
    "file_download",
    "file_edit",
    "file_share",
    "super_search",
    "compression",
    "p2p_transfer",
    "favorites",
    "storage_quotas",
    "email_notifications",
    "transfer_sendfile",
    "folder_create",
    "folder_delete",
    "allow_simple_passwords",
    "abac_engine",
    "abac_audit_decisions",
    "webauthn",
    "onedrive_backup",
    "onedrive_browser",
    "smb_server",
    "webdav_server",
)


def _admin_checkbox(handler: BaseHandler, name: str) -> bool:
    return handler.get_argument(name, "off") == "on"


def _apply_admin_feature_flags(handler: BaseHandler) -> str:
    for name in _ADMIN_FEATURE_FLAG_NAMES:
        FEATURE_FLAGS[name] = _admin_checkbox(handler, name)
    plugin_notice = ""
    direct_messages_requested = _admin_checkbox(handler, "direct_messages")
    if direct_messages_requested and not chat_library_available():
        FEATURE_FLAGS["direct_messages"] = False
        plugin_notice = "chat_package_required"
    else:
        FEATURE_FLAGS["direct_messages"] = direct_messages_requested
    gitlab_requested = _admin_checkbox(handler, "gitlab_integration")
    if gitlab_requested and not gitlab_library_available():
        FEATURE_FLAGS["gitlab_integration"] = False
        plugin_notice = plugin_notice or "gitlab_package_required"
    else:
        FEATURE_FLAGS["gitlab_integration"] = gitlab_requested
    return plugin_notice


def _apply_admin_websocket_config(handler: BaseHandler) -> None:
    websocket_config = {}
    try:
        websocket_config["feature_flags_max_connections"] = max(
            1,
            min(1000, int(handler.get_argument("feature_flags_max_connections", "50"))),
        )
        websocket_config["feature_flags_idle_timeout"] = max(
            30,
            min(7200, int(handler.get_argument("feature_flags_idle_timeout", "600"))),
        )
        websocket_config["file_streaming_max_connections"] = max(
            1,
            min(
                1000,
                int(handler.get_argument("file_streaming_max_connections", "200")),
            ),
        )
        websocket_config["file_streaming_idle_timeout"] = max(
            30,
            min(7200, int(handler.get_argument("file_streaming_idle_timeout", "300"))),
        )
        websocket_config["search_max_connections"] = max(
            1, min(1000, int(handler.get_argument("search_max_connections", "100")))
        )
        websocket_config["search_idle_timeout"] = max(
            30, min(7200, int(handler.get_argument("search_idle_timeout", "180")))
        )
        WEBSOCKET_CONFIG.update(websocket_config)
    except (ValueError, TypeError):
        pass


def _apply_admin_upload_config(
    handler: BaseHandler, requested_profile: str, profile_changed: bool
) -> None:
    try:
        max_file_size_mb = max(1, int(handler.get_argument("max_file_size_mb", "10240")))
        UPLOAD_CONFIG["max_file_size_mb"] = max_file_size_mb
        if profile_changed:
            constants_module.apply_transfer_profile_defaults(requested_profile)
        else:
            single_request_max_mb = int(
                handler.get_argument(
                    "single_request_max_mb",
                    str(UPLOAD_CONFIG.get("single_request_max_mb", 100)),
                )
            )
            if single_request_max_mb <= 0:
                UPLOAD_CONFIG["single_request_max_mb"] = 0
            else:
                UPLOAD_CONFIG["single_request_max_mb"] = min(
                    single_request_max_mb, max_file_size_mb
                )
            range_chunk_mb = max(
                4,
                min(
                    200,
                    int(
                        handler.get_argument(
                            "range_chunk_mb",
                            str(UPLOAD_CONFIG.get("range_chunk_mb", 90)),
                        )
                    ),
                ),
            )
            UPLOAD_CONFIG["range_chunk_mb"] = range_chunk_mb
            UPLOAD_CONFIG["range_upload_concurrency"] = max(
                1,
                min(
                    64,
                    int(
                        handler.get_argument(
                            "range_upload_concurrency",
                            str(
                                UPLOAD_CONFIG.get("range_upload_concurrency", 16)
                            ),
                        )
                    ),
                ),
            )
        ws_chunk_mb = max(1, min(200, int(handler.get_argument("ws_chunk_mb", "90"))))
        UPLOAD_CONFIG["ws_chunk_mb"] = ws_chunk_mb
        constants_module.set_transfer_profile(requested_profile)
        constants_module.refresh_upload_derived_constants()
        UPLOAD_CONFIG["allow_all_file_types"] = (
            1 if handler.get_argument("allow_all_file_types", "off") == "on" else 0
        )
    except (ValueError, TypeError):
        pass


def _resolve_transfer_profile_submission(
    handler: BaseHandler, current_runtime: dict
) -> tuple[str | None, bool]:
    request_arguments = getattr(handler.request, "arguments", {})
    submitted = isinstance(request_arguments, dict) and (
        "transfer_profile" in request_arguments
        or b"transfer_profile" in request_arguments
    )
    configured = current_runtime.get("configuredProfile", "open")
    requested = (
        handler.get_argument("transfer_profile", configured).strip().lower()
        if submitted
        else configured
    )
    if requested not in _VALID_TRANSFER_PROFILES:
        return None, False
    return requested, submitted and configured != requested


def _persist_admin_runtime_config(
    handler: BaseHandler, requested_profile: str
) -> dict | None:
    """Persist admin form config to DB. Return runtime payload or None."""
    db_conn = handler.db_conn
    if db_conn is None:
        return None
    cfg = handler.get_service("config_service")
    cfg.save_feature_flags(db_conn, FEATURE_FLAGS)
    persisted_flags = cfg.load_feature_flags(db_conn)
    if persisted_flags:
        FEATURE_FLAGS.update(persisted_flags)
    invalidate_feature_flags_cache()
    cfg.save_websocket_config(db_conn, WEBSOCKET_CONFIG)
    invalidate_websocket_config_cache()
    cfg.save_upload_config(db_conn, UPLOAD_CONFIG)
    cfg.sync_upload_config_from_db(db_conn)
    cfg.save_transfer_limits(db_conn, TRANSFER_CONFIG)
    cfg.save_compression_config(db_conn, COMPRESSION_CONFIG)
    runtime_config = cfg.save_transfer_profile(db_conn, requested_profile)
    if not UPLOAD_CONFIG.get("allow_all_file_types"):
        selected_extensions = {
            e for e in handler.get_arguments("allow_ext") if e and e.startswith(".")
        }
        cfg.save_allowed_extensions(db_conn, selected_extensions)
        constants_module.UPLOAD_ALLOWED_EXTENSIONS = selected_extensions
    return runtime_config if isinstance(runtime_config, dict) else None


def _runtime_payload_or_fallback(runtime_config: dict | None) -> dict:
    if isinstance(runtime_config, dict):
        return runtime_config
    try:
        runtime_payload = constants_module.get_effective_transfer_strategy()
    except Exception:
        runtime_payload = {}
    return runtime_payload if isinstance(runtime_payload, dict) else {}


def _parse_plugin_usernames(raw: str) -> list[str]:
    from aird.constants.input_limits import PLUGIN_MAX_USERNAMES, PLUGIN_USERNAME_MAX_LEN

    names: list[str] = []
    seen: set[str] = set()
    for part in (raw or "").replace(",", " ").split():
        name = part.strip()
        if not name or len(name) > PLUGIN_USERNAME_MAX_LEN or name in seen:
            continue
        seen.add(name)
        names.append(name)
        if len(names) >= PLUGIN_MAX_USERNAMES:
            break
    return names


def _save_plugin_scope_assignments(handler: BaseHandler, conn) -> None:
    from aird.plugins.access import (
        SCOPE_ALL,
        SCOPE_NONE,
        SCOPE_USERS,
        catalog,
        set_assignment,
    )

    allowed = {SCOPE_NONE, SCOPE_ALL, SCOPE_USERS}
    for item in catalog():
        pid = item["id"]
        scope = (handler.get_argument(f"scope_{pid}", SCOPE_NONE) or SCOPE_NONE).strip()
        if scope not in allowed:
            scope = SCOPE_NONE
        names = _parse_plugin_usernames(handler.get_argument(f"usernames_{pid}", "") or "")
        set_assignment(conn, pid, scope, names)


def _save_onedrive_plugin_settings(handler: BaseHandler, conn) -> None:
    from aird.plugins.onedrive.settings import (
        clear_token_blob,
        save_settings,
        save_stored_access_token,
    )
    from aird.plugins.onedrive_browser.settings import save_settings as save_browser_settings

    save_settings(
        conn,
        root_path=handler.get_argument("onedrive_root_path", "") or "",
        sync_interval_minutes=handler.get_argument("onedrive_sync_interval", "") or 10,
        auth_source=handler.get_argument("onedrive_auth_source", "") or "",
        client_id=handler.get_argument("onedrive_client_id", "") or "",
        tenant=handler.get_argument("onedrive_tenant", "") or "common",
        token_env_var=handler.get_argument("onedrive_token_env_var", "") or "",
    )
    if handler.get_argument("onedrive_token_clear", "") == "1":
        clear_token_blob(conn)
    od_token = (handler.get_argument("onedrive_token", "") or "").strip()
    if od_token:
        save_stored_access_token(conn, od_token)
    save_browser_settings(
        conn,
        client_id=handler.get_argument("onedrive_browser_client_id", "") or "",
        tenant=handler.get_argument("onedrive_browser_tenant", "") or "common",
    )


def _save_gitlab_plugin_settings(handler: BaseHandler, conn) -> None:
    import json as _json

    from aird.plugins.gitlab.settings import (
        clear_stored_token,
        normalize_boards,
        save_settings as save_gitlab_settings,
        save_stored_token,
    )

    boards_raw = handler.get_argument("gitlab_boards_json", "") or "[]"
    try:
        boards = normalize_boards(_json.loads(boards_raw))
    except (TypeError, ValueError):
        boards = []
    save_gitlab_settings(
        conn,
        default_host=handler.get_argument("gitlab_default_host", "") or "",
        boards=boards,
        token_source=handler.get_argument("gitlab_token_source", "") or "",
        token_env_var=handler.get_argument("gitlab_token_env_var", "") or "",
    )
    if handler.get_argument("gitlab_token_clear", "") == "1":
        clear_stored_token(conn)
    token = (handler.get_argument("gitlab_token", "") or "").strip()
    if token:
        save_stored_token(conn, token)


class AdminHandler(BaseHandler):
    @tornado.web.authenticated
    @require_admin(redirect_url=URL_ADMIN_LOGIN)
    def get(self):

        invalidate_feature_flags_cache()
        invalidate_websocket_config_cache()
        current_features = get_current_feature_flags()
        current_websocket_config = get_current_websocket_config()

        # Check if LDAP is enabled
        ldap_enabled = self.settings.get("ldap_server") is not None
        # For "allowed file types" UI: list of options and currently allowed set
        db_conn = self.db_conn
        allowed_current = (
            self.get_service("config_service").load_allowed_extensions(db_conn)
            if db_conn
            else set(constants_module.UPLOAD_ALLOWED_EXTENSIONS)
        )
        available_extensions = sorted(constants_module.ALLOWED_UPLOAD_EXTENSIONS)

        runtime_config = self.get_service("config_service").get_runtime_config(db_conn)

        self.render(
            "admin.html",
            features=current_features,
            websocket_config=current_websocket_config,
            upload_config=UPLOAD_CONFIG,
            runtime_config=runtime_config,
            transfer_profile_names=_VALID_TRANSFER_PROFILES,
            ldap_enabled=ldap_enabled,
            available_extensions=available_extensions,
            allowed_extensions_current=allowed_current,
            smb_library_available=smb_library_available(),
            webdav_library_available=webdav_library_available(),
            chat_library_available=chat_library_available(),
            gitlab_library_available=gitlab_library_available(),
            admin_notice=self.get_argument("notice", ""),
        )

    @tornado.web.authenticated
    @require_admin(deny_status=HTTP_FORBIDDEN, deny_body=ACCESS_DENIED)
    def post(self):
        current_runtime = self.get_service("config_service").get_runtime_config(
            self.db_conn
        )
        requested_profile, profile_changed = _resolve_transfer_profile_submission(
            self, current_runtime
        )
        if requested_profile is None:
            self.set_status(HTTP_BAD_REQUEST)
            self.write("Invalid transfer profile")
            return

        plugin_notice = _apply_admin_feature_flags(self)
        _apply_admin_websocket_config(self)
        _apply_admin_upload_config(self, requested_profile, profile_changed)

        runtime_config = None
        try:
            runtime_config = _persist_admin_runtime_config(self, requested_profile)
        except Exception:
            logging.warning("admin config save failed", exc_info=True)

        runtime_payload = _runtime_payload_or_fallback(runtime_config)
        FeatureFlagSocketHandler.send_updates()
        RuntimeConfigSocketHandler.send_updates(runtime_payload)
        if plugin_notice:
            self.redirect(f"{URL_ADMIN}?notice={plugin_notice}")
        else:
            self.redirect(URL_ADMIN)


class AdminFeatureFlagAPIHandler(XSRFTokenMixin, BaseHandler):
    """POST /admin/api/feature-flags — save one toggle without a full form submit."""

    @tornado.web.authenticated
    @require_admin(deny_status=HTTP_FORBIDDEN, deny_body=ACCESS_DENIED_JSON)
    def post(self):
        self.check_xsrf_cookie()
        payload = self.parse_json_body() or {}
        key = str(payload.get("flag") or payload.get("key") or "").strip()
        raw = payload.get("enabled", payload.get("value"))
        if isinstance(raw, str):
            enabled = raw.strip().lower() in {"1", "true", "on", "yes"}
        else:
            enabled = bool(raw)
        result = self.get_service("config_service").apply_feature_flag(
            self.db_conn, key, enabled
        )
        self.set_header("Content-Type", CONTENT_TYPE_JSON)
        if not result.get("ok"):
            self.set_status(int(result.get("status") or HTTP_BAD_REQUEST))
        self.write(result)


class AdminPluginsHandler(BaseHandler):
    @tornado.web.authenticated
    @require_admin(redirect_url=URL_ADMIN_LOGIN)
    def get(self):
        from aird.plugins.access import catalog, get_assignment
        from aird.utils.util import get_current_feature_flags

        conn = self.db_conn
        flags = get_current_feature_flags()
        plugins = []
        for item in catalog():
            assignment = get_assignment(conn, item["id"])
            plugins.append(
                {
                    **item,
                    **assignment,
                    "flag_on": bool(flags.get(item["flag"])),
                    "usernames_text": "\n".join(assignment.get("usernames") or []),
                }
            )
        users = []
        if conn is not None:
            try:
                users = _get_user_service(self).list_users(conn) or []
            except Exception:
                users = []
        usernames = [u.get("username") for u in users if u.get("username")]
        from aird.plugins.onedrive.settings import get_settings
        from aird.plugins.onedrive_browser.settings import get_settings as get_browser_settings
        from aird.plugins.gitlab.settings import get_settings as get_gitlab_settings

        selected = (self.get_argument("p", "") or self.get_argument("selected_plugin", "") or "").strip()
        if not selected and plugins:
            selected = plugins[0]["id"]

        self.render(
            "admin_plugins.html",
            plugins=plugins,
            usernames=usernames,
            saved=self.get_argument("saved", "") == "1",
            selected_plugin=selected,
            ldap_enabled=self.settings.get("ldap_server") is not None,
            onedrive_settings=get_settings(conn),
            onedrive_browser_settings=get_browser_settings(conn),
            gitlab_settings=get_gitlab_settings(conn),
        )

    @tornado.web.authenticated
    @require_admin(deny_status=HTTP_FORBIDDEN, deny_body=ACCESS_DENIED)
    def post(self):
        conn = self.db_conn
        if conn is None:
            self.set_status(503)
            self.write("Database not available")
            return
        _save_plugin_scope_assignments(self, conn)
        _save_onedrive_plugin_settings(self, conn)
        _save_gitlab_plugin_settings(self, conn)
        selected = (self.get_argument("selected_plugin", "") or "").strip()
        suffix = f"&p={selected}" if selected else ""
        self.redirect(f"{URL_ADMIN_PLUGINS}?saved=1{suffix}")


class AdminOneDriveDeviceStartHandler(BaseHandler, XSRFTokenMixin):
    @tornado.web.authenticated
    @require_admin(deny_status=HTTP_FORBIDDEN, deny_body=ACCESS_DENIED)
    @require_db
    def post(self):
        self.check_xsrf_cookie()
        conn = self.db_conn
        from aird.plugins.onedrive.auth import OneDriveAuthError, start_device_flow
        from aird.plugins.onedrive.settings import get_settings, save_device_pending

        settings = get_settings(conn)
        try:
            flow = start_device_flow(settings["client_id"], settings["tenant"])
        except OneDriveAuthError as exc:
            self.set_status(400)
            self.write({"ok": False, "error": str(exc)})
            return
        save_device_pending(conn, flow)
        self.write(
            {
                "ok": True,
                "user_code": flow.get("user_code"),
                "verification_uri": flow.get("verification_uri"),
                "interval": flow.get("interval"),
                "expires_in": flow.get("expires_in"),
            }
        )


class AdminOneDriveDevicePollHandler(BaseHandler, XSRFTokenMixin):
    @tornado.web.authenticated
    @require_admin(deny_status=HTTP_FORBIDDEN, deny_body=ACCESS_DENIED)
    @require_db
    def post(self):
        self.check_xsrf_cookie()
        conn = self.db_conn
        from aird.plugins.onedrive.auth import OneDriveAuthError, poll_device_flow
        from aird.plugins.onedrive.settings import (
            clear_device_pending,
            get_settings,
            load_device_pending,
            save_device_tokens,
        )

        settings = get_settings(conn)
        pending = load_device_pending(conn)
        device_code = pending.get("device_code")
        if not device_code:
            self.set_status(400)
            self.write({"ok": False, "error": "No device login in progress"})
            return
        try:
            tokens = poll_device_flow(
                settings["client_id"], settings["tenant"], device_code
            )
        except OneDriveAuthError as exc:
            if exc.pending:
                self.write({"ok": False, "pending": True})
                return
            clear_device_pending(conn)
            self.set_status(400)
            self.write({"ok": False, "error": str(exc)})
            return
        save_device_tokens(conn, tokens)
        from aird.plugins.onedrive_host.token_store import save_file_token
        from aird.plugins.onedrive_host.watcher import reload_watchers

        save_file_token(tokens)
        reload_watchers(conn)
        self.write({"ok": True, "token_configured": True})


class WebSocketStatsHandler(BaseHandler):
    @tornado.web.authenticated
    @require_admin(deny_status=HTTP_FORBIDDEN, deny_body="Forbidden")
    def get(self):
        """Return WebSocket connection statistics"""

        stats = {
            "feature_flags": FeatureFlagSocketHandler.connection_manager.get_stats(),
            "file_streaming": FileStreamHandler.connection_manager.get_stats(),
            "super_search": SuperSearchWebSocketHandler.connection_manager.get_stats(),
            "timestamp": time.time(),
        }

        self.set_header("Content-Type", CONTENT_TYPE_JSON)
        self.write(json.dumps(stats, indent=2))


class AdminAuditHandler(BaseHandler):
    @tornado.web.authenticated
    @require_admin(redirect_url=URL_ADMIN_LOGIN)
    def get(self):
        """Display audit log (admin only). ?format=csv for export."""
        db_conn = self.db_conn
        limit = min(1000, max(1, int(self.get_argument("limit", "500"))))
        offset = max(0, int(self.get_argument("offset", "0")))
        if self.get_argument("format", "") == "csv":
            self.set_header("Content-Type", CONTENT_TYPE_CSV)
            self.set_header("Content-Disposition", "attachment; filename=audit_log.csv")
            rows = self.get_service("audit_service").get_logs(
                db_conn, limit=10000, offset=0
            )
            buf = io.StringIO()
            w = csv.writer(buf)
            w.writerow(["id", "created_at", "username", "action", "details", "ip"])
            for r in rows:
                w.writerow(
                    [
                        r.get("id"),
                        r.get("created_at"),
                        r.get("username") or "",
                        r.get("action") or "",
                        r.get("details") or "",
                        r.get("ip") or "",
                    ]
                )
            self.write(buf.getvalue())
            return
        logs = self.get_service("audit_service").get_logs(
            db_conn, limit=limit, offset=offset
        )
        self.render(
            "admin_audit.html",
            logs=logs,
            limit=limit,
            offset=offset,
            logs_count=len(logs),
        )


class AdminUsersHandler(BaseHandler):
    @tornado.web.authenticated
    @require_admin(redirect_url=URL_ADMIN_LOGIN)
    def get(self):
        """Display user management interface"""

        users = []
        db_conn = self.db_conn
        user_service = _get_user_service(self)
        if db_conn is not None:
            if user_service is not None:
                users = user_service.list_users(db_conn)
            else:
                users = self.get_service("user_service").list_users(db_conn)

        self.render(
            "admin_users.html",
            users=users,
            reset_password_plain=None,
            reset_for_username=None,
        )


class UserCreateHandler(BaseHandler):
    @tornado.web.authenticated
    @require_admin(deny_status=HTTP_FORBIDDEN, deny_body=ACCESS_DENIED)
    def get(self):
        """Show create user form"""

        self.render(TEMPLATE_USER_CREATE, error=None)

    @tornado.web.authenticated
    @require_admin(deny_status=HTTP_FORBIDDEN, deny_body=ACCESS_DENIED)
    def post(self):
        """Create a new user"""

        db_conn = self.db_conn
        user_service = _get_user_service(self)
        if db_conn is None:
            self.render(TEMPLATE_USER_CREATE, error=DATABASE_NOT_AVAILABLE)
            return

        username = self.get_argument("username", "").strip()
        password = self.get_argument("password", "").strip()
        role = self.get_argument("role", "user").strip()

        # Input validation
        if not username or not password:
            self.render(TEMPLATE_USER_CREATE, error=USERNAME_PASSWORD_REQUIRED)
            return

        if len(username) < 3 or len(username) > 50:
            self.render(TEMPLATE_USER_CREATE, error=USERNAME_LENGTH)
            return

        is_valid, error = validate_password(password)
        if not is_valid:
            self.render(TEMPLATE_USER_CREATE, error=error)
            return

        if role not in ["user", "admin"]:
            self.render(TEMPLATE_USER_CREATE, error=INVALID_ROLE)
            return

        # Check for valid username format (alphanumeric + underscore/hyphen)
        if not re.match(r"^[a-zA-Z0-9_-]+$", username):
            self.render(
                TEMPLATE_USER_CREATE,
                error="Username can only contain letters, numbers, underscores, and hyphens",
            )
            return

        try:
            if user_service is not None:
                user_service.create_user(db_conn, username, password, role=role)
            else:
                self.get_service("user_service").create_user(
                    db_conn, username, password, role
                )
            self.redirect(URL_ADMIN_USERS)
        except ValueError as e:
            self.render(TEMPLATE_USER_CREATE, error=str(e))
        except Exception:
            self.render(TEMPLATE_USER_CREATE, error=FAILED_CREATE_USER)


def _validate_user_edit(
    username: str,
    password: str,
    role: str,
    settings: dict,
) -> str | None:
    """Validate user edit form. Returns error message or None if valid."""
    if not username:
        return USERNAME_REQUIRED
    if len(username) < 3 or len(username) > 50:
        return USERNAME_LENGTH
    if password:
        is_valid, error = validate_password(password)
        if not is_valid:
            return error
    if role not in ["user", "admin"]:
        return INVALID_ROLE
    if not re.match(r"^[a-zA-Z0-9_-]+$", username):
        return "Username can only contain letters, numbers, underscores, and hyphens"
    if password and settings.get("ldap_server"):
        return LDAP_PASSWORD_CHANGE
    return None


class UserEditHandler(BaseHandler):
    @tornado.web.authenticated
    @require_admin(deny_status=HTTP_FORBIDDEN, deny_body=ACCESS_DENIED)
    @require_db
    def get(self, user_id):
        """Show edit user form"""

        try:
            user_id = int(user_id)
            # Get user by ID
            user_service = _get_user_service(self)
            if user_service is not None:
                users = user_service.list_users(self.db_conn)
            else:
                users = self.get_service("user_service").list_users(self.db_conn)
            user = next((u for u in users if u["id"] == user_id), None)

            if not user:
                self.set_status(404)
                self.write(USER_NOT_FOUND)
                return

            self.render(
                TEMPLATE_USER_EDIT, user=user, error=None, settings=self.settings
            )
        except ValueError:
            self.set_status(HTTP_BAD_REQUEST)
            self.write(INVALID_USER_ID)

    @tornado.web.authenticated
    @require_admin(deny_status=HTTP_FORBIDDEN, deny_body=ACCESS_DENIED)
    @require_db
    def post(self, user_id):
        """Update user information"""
        user = None
        try:
            user_id = int(user_id)
            # Get existing user
            user_service = _get_user_service(self)
            if user_service is not None:
                users = user_service.list_users(self.db_conn)
            else:
                users = self.get_service("user_service").list_users(self.db_conn)
            user = next((u for u in users if u["id"] == user_id), None)

            if not user:
                self.set_status(404)
                self.write(USER_NOT_FOUND)
                return

            username = self.get_argument("username", "").strip()
            password = self.get_argument("password", "").strip()
            role = self.get_argument("role", "user").strip()
            active = self.get_argument("active", "off") == "on"

            error = _validate_user_edit(username, password, role, self.settings)
            if error:
                self.render(TEMPLATE_USER_EDIT, user=user, error=error, settings=self.settings)
                return

            update_data = {"username": username, "role": role, "active": active}
            if password:
                update_data["password"] = password
                update_data["must_change_password"] = False

            if user_service is not None:
                updated = user_service.update_user(self.db_conn, user_id, **update_data)
            else:
                updated = self.get_service("user_service").update_user(
                    self.db_conn, user_id, **update_data
                )
            if updated:
                self.redirect(URL_ADMIN_USERS)
            else:
                self.render(TEMPLATE_USER_EDIT, user=user, error=FAILED_UPDATE_USER, settings=self.settings)

        except ValueError:
            self.set_status(HTTP_BAD_REQUEST)
            self.write(INVALID_USER_ID)
        except Exception:
            logging.exception("User update error")
            if user is not None:
                self.render(
                    TEMPLATE_USER_EDIT,
                    user=user,
                    error=ERROR_UPDATE_USER,
                    settings=self.settings,
                )
            else:
                self.set_status(500)
                self.write(ERROR_UPDATE_USER)


class UserDeleteHandler(BaseHandler):
    @tornado.web.authenticated
    @require_admin(deny_status=HTTP_FORBIDDEN, deny_body=ACCESS_DENIED)
    @require_db
    def post(self):
        """Delete a user"""

        try:
            user_id = int(self.get_argument("user_id", "0"))
            user_service = _get_user_service(self)

            if user_id <= 0:
                self.set_status(HTTP_BAD_REQUEST)
                self.write(INVALID_USER_ID_SHORT)
                return

            if user_service is not None:
                deleted = user_service.delete_user(self.db_conn, user_id)
            else:
                deleted = self.get_service("user_service").delete_user(
                    self.db_conn, user_id
                )
            if deleted:
                self.redirect(URL_ADMIN_USERS)
            else:
                self.set_status(404)
                self.write(USER_NOT_FOUND)

        except ValueError:
            self.set_status(HTTP_BAD_REQUEST)
            self.write(INVALID_USER_ID)


class UserPasswordResetHandler(BaseHandler):
    @tornado.web.authenticated
    @require_admin(deny_status=HTTP_FORBIDDEN, deny_body=ACCESS_DENIED)
    @require_db
    def post(self):
        """Set a temporary password; user must replace it after login."""

        try:
            user_id = int(self.get_argument("user_id", "0"))
        except ValueError:
            self.set_status(HTTP_BAD_REQUEST)
            self.write(INVALID_USER_ID_SHORT)
            return
        if user_id <= 0:
            self.set_status(HTTP_BAD_REQUEST)
            self.write(INVALID_USER_ID_SHORT)
            return

        user_service = _get_user_service(self)
        temp_password = _secrets.token_urlsafe(32)
        if not user_service.update_user(
            self.db_conn,
            user_id,
            password=temp_password,
            must_change_password=True,
        ):
            self.set_status(500)
            self.write("Password reset failed")
            return

        refreshed = user_service.list_users(self.db_conn)
        target = next((u for u in refreshed if u["id"] == user_id), None)
        if not target:
            self.set_status(404)
            self.write(USER_NOT_FOUND)
            return

        try:
            self.get_service("audit_service").log(
                self.db_conn,
                "admin_password_reset",
                username=target["username"],
                ip=self.request.remote_ip,
            )
        except Exception:
            logging.debug("audit admin_password_reset failed", exc_info=True)

        self.render(
            "admin_users.html",
            users=refreshed,
            reset_password_plain=temp_password,
            reset_for_username=target["username"],
        )


class LDAPConfigHandler(BaseHandler):
    @tornado.web.authenticated
    @require_admin(redirect_url=URL_ADMIN_LOGIN)
    def get(self):
        """Display LDAP configuration management interface"""

        configs = []
        sync_logs = []
        db_conn = self.db_conn
        if db_conn is not None:
            configs = get_all_ldap_configs(db_conn)
            sync_logs = get_ldap_sync_logs(db_conn, limit=20)

        self.render("admin_ldap.html", configs=configs, sync_logs=sync_logs)


class LDAPConfigCreateHandler(BaseHandler):
    @tornado.web.authenticated
    @require_admin(deny_status=HTTP_FORBIDDEN, deny_body=ACCESS_DENIED)
    def get(self):
        """Show create LDAP configuration form"""

        self.render(TEMPLATE_LDAP_CONFIG_CREATE, error=None)

    @tornado.web.authenticated
    @require_admin(deny_status=HTTP_FORBIDDEN, deny_body=ACCESS_DENIED)
    @require_db
    def post(self):
        """Create a new LDAP configuration"""

        name = self.get_argument("name", "").strip()
        server = self.get_argument("server", "").strip()
        ldap_base_dn = self.get_argument("ldap_base_dn", "").strip()
        ldap_member_attributes = self.get_argument(
            "ldap_member_attributes", "member"
        ).strip()
        user_template = self.get_argument("user_template", "").strip()

        # Input validation
        if not all([name, server, ldap_base_dn, user_template]):
            self.render(TEMPLATE_LDAP_CONFIG_CREATE, error=ALL_FIELDS_REQUIRED)
            return

        if len(name) < 3 or len(name) > 50:
            self.render(
                TEMPLATE_LDAP_CONFIG_CREATE,
                error=CONFIG_NAME_LENGTH,
            )
            return

        try:
            create_ldap_config(
                self.db_conn,
                name,
                server,
                ldap_base_dn,
                ldap_member_attributes,
                user_template,
            )
            self.redirect(URL_ADMIN_LDAP)
        except ValueError as e:
            self.render(TEMPLATE_LDAP_CONFIG_CREATE, error=str(e))
        except Exception:
            logging.exception("LDAP config creation error")
            self.render(
                TEMPLATE_LDAP_CONFIG_CREATE,
                error="Error creating configuration. Please try again.",
            )


class LDAPConfigEditHandler(BaseHandler):
    @tornado.web.authenticated
    @require_admin(deny_status=HTTP_FORBIDDEN, deny_body=ACCESS_DENIED)
    @require_db
    def get(self, config_id):
        """Show edit LDAP configuration form"""

        try:
            config_id = int(config_id)
            config = get_ldap_config_by_id(self.db_conn, config_id)

            if not config:
                self.set_status(404)
                self.write(CONFIG_NOT_FOUND)
                return

            self.render(TEMPLATE_LDAP_CONFIG_EDIT, config=config, error=None)
        except ValueError:
            self.write(INVALID_CONFIG_ID)

    @tornado.web.authenticated
    @require_admin(deny_status=HTTP_FORBIDDEN, deny_body=ACCESS_DENIED)
    @require_db
    def post(self, config_id):
        """Update LDAP configuration"""

        try:
            config_id = int(config_id)
            config = get_ldap_config_by_id(self.db_conn, config_id)

            if not config:
                self.set_status(404)
                self.write(CONFIG_NOT_FOUND)
                return

            name = self.get_argument("name", "").strip()
            server = self.get_argument("server", "").strip()
            ldap_base_dn = self.get_argument("ldap_base_dn", "").strip()
            ldap_member_attributes = self.get_argument(
                "ldap_member_attributes", "member"
            ).strip()
            user_template = self.get_argument("user_template", "").strip()
            active = self.get_argument("active", "off") == "on"

            # Input validation
            if not all([name, server, ldap_base_dn, user_template]):
                self.render(
                    TEMPLATE_LDAP_CONFIG_EDIT,
                    config=config,
                    error=ALL_FIELDS_REQUIRED,
                )
                return

            if len(name) < 3 or len(name) > 50:
                self.render(
                    TEMPLATE_LDAP_CONFIG_EDIT,
                    config=config,
                    error=CONFIG_NAME_LENGTH,
                )
                return

            # Update configuration
            if update_ldap_config(
                self.db_conn,
                config_id,
                name=name,
                server=server,
                ldap_base_dn=ldap_base_dn,
                ldap_member_attributes=ldap_member_attributes,
                user_template=user_template,
                active=active,
            ):
                self.redirect(URL_ADMIN_LDAP)
            else:
                self.render(
                    TEMPLATE_LDAP_CONFIG_EDIT,
                    config=config,
                    error="Failed to update configuration",
                )

        except ValueError:
            self.write(INVALID_CONFIG_ID)
        except Exception:
            logging.exception("LDAP config update error")
            self.render(
                TEMPLATE_LDAP_CONFIG_EDIT,
                config=config,
                error=ERROR_UPDATE_CONFIG,
            )


class LDAPConfigDeleteHandler(BaseHandler):
    @tornado.web.authenticated
    @require_admin(deny_status=HTTP_FORBIDDEN, deny_body=ACCESS_DENIED)
    @require_db
    def post(self):
        """Delete LDAP configuration"""

        try:
            config_id = int(self.get_argument("config_id", "0"))

            if config_id <= 0:
                self.set_status(HTTP_BAD_REQUEST)
                self.write(INVALID_CONFIG_ID)
                return

            if delete_ldap_config(self.db_conn, config_id):
                self.redirect(URL_ADMIN_LDAP)
            else:
                self.set_status(404)
                self.write(CONFIG_NOT_FOUND)

        except ValueError:
            self.set_status(HTTP_BAD_REQUEST)
            self.write(INVALID_CONFIG_ID)


class LDAPSyncHandler(BaseHandler):
    @tornado.web.authenticated
    @require_admin(deny_status=HTTP_FORBIDDEN, deny_body={"error": ACCESS_DENIED_JSON})
    def post(self):

        # In a real application, you would trigger the LDAP sync here.
        # For now, we'll just return a success message.
        self.write({"status": SYNC_STARTED})


# -------------------------------------------------------
# Network Shares (SMB / WebDAV)
# -------------------------------------------------------


class AdminNetworkSharesHandler(BaseHandler):
    @tornado.web.authenticated
    @require_admin(redirect_url=URL_ADMIN_LOGIN)
    def get(self):
        db_conn = self.db_conn
        shares = (
            self.get_service("network_share_service").list_all(db_conn)
            if db_conn
            else []
        )
        mgr = self.network_share_manager
        for idx, s in enumerate(shares):
            s["running"] = mgr.is_running(s["id"]) if mgr else False
            s["mount_idx"] = idx
        error = self.get_argument("error", None)
        # Resolve a network-reachable address for mount commands
        server_host = _socket.getfqdn()
        if server_host in ("localhost", "localhost.localdomain", ""):
            try:
                server_host = _socket.gethostbyname(_socket.gethostname())
            except Exception:
                server_host = "127.0.0.1"
        self.render(
            "admin_network_shares.html",
            shares=shares,
            error=error,
            server_host=server_host,
            server_host_js=json.dumps(server_host),
            smb_server_available=is_smb_server_available(),
            webdav_server_available=is_webdav_server_available(),
        )

    @tornado.web.authenticated
    @require_admin(deny_status=HTTP_FORBIDDEN)
    def post(self):
        db_conn = self.db_conn
        if db_conn is None:
            self.redirect(f"{URL_ADMIN_NETWORK_SHARES}?error={ERR_DB_UNAVAILABLE}")
            return

        name = self.get_argument("name", "").strip()
        folder_path = self.get_argument("folder_path", "").strip()
        protocol = self.get_argument("protocol", "webdav").strip().lower()
        username = self.get_argument("share_username", "").strip()
        password = self.get_argument("share_password", "").strip()
        read_only = self.get_argument("read_only", "off") == "on"

        try:
            port = int(self.get_argument("port", "8443"))
        except (ValueError, TypeError):
            port = 8443

        if not name or not folder_path or not username or not password:
            self.redirect(f"{URL_ADMIN_NETWORK_SHARES}?error={ERR_ALL_FIELDS_REQUIRED}")
            return
        if protocol not in ("smb", "webdav"):
            self.redirect(f"{URL_ADMIN_NETWORK_SHARES}?error={ERR_INVALID_PROTOCOL}")
            return
        if protocol == "smb" and not is_smb_server_available():
            self.redirect(f"{URL_ADMIN_NETWORK_SHARES}?error={ERR_SMB_UNAVAILABLE}")
            return
        if protocol == "webdav" and not is_webdav_server_available():
            self.redirect(f"{URL_ADMIN_NETWORK_SHARES}?error={ERR_WEBDAV_UNAVAILABLE}")
            return
        if not os.path.isdir(folder_path):
            self.redirect("/admin/network-shares?error=Folder+does+not+exist")
            return
        if port < 1 or port > 65535:
            self.redirect(f"{URL_ADMIN_NETWORK_SHARES}?error={ERR_PORT_RANGE}")
            return

        share_id = _secrets.token_urlsafe(8)
        ok = self.get_service("network_share_service").create(
            db_conn,
            share_id,
            name,
            folder_path,
            protocol,
            port,
            username,
            password,
            read_only,
        )
        if not ok:
            self.redirect(f"{URL_ADMIN_NETWORK_SHARES}?error={ERR_FAILED_CREATE_SHARE}")
            return

        self.get_service("audit_service").log(
            db_conn,
            "network_share_create",
            username=self.get_display_username(),
            details=f"name={name} protocol={protocol} port={port} folder={folder_path}",
            ip=self.request.remote_ip,
        )

        mgr = self.network_share_manager
        if mgr:
            share_dict = {
                "id": share_id,
                "name": name,
                "folder_path": folder_path,
                "protocol": protocol,
                "port": port,
                "username": username,
                "password": password,
                "read_only": read_only,
            }
            mgr.start_share(share_dict)

        self.redirect(URL_ADMIN_NETWORK_SHARES)


class AdminNetworkShareDeleteHandler(BaseHandler):
    @tornado.web.authenticated
    @require_admin(deny_status=HTTP_FORBIDDEN)
    def post(self):
        db_conn = self.db_conn
        share_id = self.get_argument("share_id", "").strip()
        if not share_id or db_conn is None:
            self.redirect(URL_ADMIN_NETWORK_SHARES)
            return

        mgr = self.network_share_manager
        if mgr:
            mgr.stop_share(share_id)

        self.get_service("network_share_service").delete(db_conn, share_id)
        self.get_service("audit_service").log(
            db_conn,
            "network_share_delete",
            username=self.get_display_username(),
            details=f"share_id={share_id}",
            ip=self.request.remote_ip,
        )
        self.redirect(URL_ADMIN_NETWORK_SHARES)


class AdminNetworkShareToggleHandler(BaseHandler):
    @tornado.web.authenticated
    @require_admin(deny_status=HTTP_FORBIDDEN)
    def post(self):
        db_conn = self.db_conn
        share_id = self.get_argument("share_id", "").strip()
        if not share_id or db_conn is None:
            self.redirect(URL_ADMIN_NETWORK_SHARES)
            return

        shares = self.get_service("network_share_service").list_all(db_conn)
        share = next((s for s in shares if s["id"] == share_id), None)
        if share is None:
            self.redirect(URL_ADMIN_NETWORK_SHARES)
            return

        new_enabled = not share["enabled"]
        if new_enabled and share.get("protocol") == "smb" and not is_smb_server_available():
            self.redirect(f"{URL_ADMIN_NETWORK_SHARES}?error={ERR_SMB_UNAVAILABLE}")
            return
        if new_enabled and share.get("protocol") == "webdav" and not is_webdav_server_available():
            self.redirect(f"{URL_ADMIN_NETWORK_SHARES}?error={ERR_WEBDAV_UNAVAILABLE}")
            return

        self.get_service("network_share_service").update(
            db_conn, share_id, enabled=new_enabled
        )

        mgr = self.network_share_manager
        if mgr:
            if new_enabled:
                mgr.start_share({**share, "enabled": True})
            else:
                mgr.stop_share(share_id)

        action = "enabled" if new_enabled else "disabled"
        self.get_service("audit_service").log(
            db_conn,
            "network_share_toggle",
            username=self.get_display_username(),
            details=f"share_id={share_id} action={action}",
            ip=self.request.remote_ip,
        )
        self.redirect(URL_ADMIN_NETWORK_SHARES)


# -------------------------------------------------------
# User path mounts (virtual browse roots)
# -------------------------------------------------------


def _admin_path_users(handler) -> list[str]:
    conn = handler.db_conn
    if not conn:
        return []
    service = _get_user_service(handler)
    users = service.list_users(conn) if service else []
    names = []
    for u in users or []:
        name = (u.get("username") or "").strip()
        if name:
            names.append(name)
    return sorted(set(names), key=str.lower)


class AdminUserPathsHandler(BaseHandler):
    @tornado.web.authenticated
    @require_admin(redirect_url=URL_ADMIN_LOGIN)
    def get(self):
        from aird.db.user_mounts import list_all_path_mounts

        db_conn = self.db_conn
        mounts = list_all_path_mounts(db_conn) if db_conn else []
        self.render(
            "admin_user_paths.html",
            mounts=mounts,
            users=_admin_path_users(self),
            error=self.get_argument("error", None),
        )

    @tornado.web.authenticated
    @require_admin(deny_status=HTTP_FORBIDDEN)
    def post(self):
        from aird.core.browse_paths import sanitize_mount_name
        from aird.db.user_mounts import ALL_USERS, insert_path_mount

        db_conn = self.db_conn
        if db_conn is None:
            self.redirect(f"{URL_ADMIN_PATHS}?error={ERR_DB_UNAVAILABLE}")
            return

        host_raw = self.get_argument("host_path", "").strip()
        mount_name = sanitize_mount_name(self.get_argument("mount_name", ""))
        writable = self.get_argument("writable", "off") == "on"
        everyone = self.get_argument("everyone", "off") == "on"
        usernames = [u.strip() for u in self.get_arguments("usernames") if u.strip()]

        if not host_raw or not os.path.isabs(host_raw):
            self.redirect(f"{URL_ADMIN_PATHS}?error=Host+path+must+be+absolute")
            return
        host_path = os.path.realpath(host_raw)
        if not os.path.isdir(host_path):
            self.redirect(f"{URL_ADMIN_PATHS}?error={ERR_FOLDER_NOT_EXIST}")
            return
        if not mount_name:
            self.redirect(f"{URL_ADMIN_PATHS}?error=Invalid+folder+name")
            return
        targets = [ALL_USERS] if everyone else usernames
        if not targets:
            self.redirect(f"{URL_ADMIN_PATHS}?error=Select+at+least+one+user")
            return

        created_by = self.get_display_username()
        for username in targets:
            mid = insert_path_mount(
                db_conn,
                username=username,
                host_path=host_path,
                mount_name=mount_name,
                writable=writable,
                created_by=created_by,
            )
            if mid is None:
                self.redirect(
                    f"{URL_ADMIN_PATHS}?error=Name+already+assigned+for+that+user"
                )
                return
        self.get_service("audit_service").log(
            db_conn,
            "user_path_mount_create",
            username=created_by,
            details=f"name={mount_name} host={host_path} users={','.join(targets)}",
            ip=self.request.remote_ip,
        )
        self.redirect(URL_ADMIN_PATHS)


class AdminUserPathDeleteHandler(BaseHandler):
    @tornado.web.authenticated
    @require_admin(deny_status=HTTP_FORBIDDEN)
    def post(self):
        from aird.db.user_mounts import delete_path_mount

        db_conn = self.db_conn
        if db_conn is None:
            self.redirect(URL_ADMIN_PATHS)
            return
        try:
            mount_id = int(self.get_argument("mount_id", "0"))
        except (TypeError, ValueError):
            self.redirect(URL_ADMIN_PATHS)
            return
        if mount_id and delete_path_mount(db_conn, mount_id):
            self.get_service("audit_service").log(
                db_conn,
                "user_path_mount_delete",
                username=self.get_display_username(),
                details=f"id={mount_id}",
                ip=self.request.remote_ip,
            )
        self.redirect(URL_ADMIN_PATHS)
