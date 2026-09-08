"""Admin HTTP API for OneDrive host sync."""

from __future__ import annotations

import tornado.web

from aird.handlers.base_handler import BaseHandler, XSRFTokenMixin, require_db
from aird.plugins.onedrive import db as od_db
from aird.plugins.onedrive.settings import (
    get_settings,
    save_device_pending,
    save_device_tokens,
    load_device_pending,
)
from aird.plugins.onedrive.auth import OneDriveAuthError, poll_device_flow, start_device_flow
from aird.plugins.onedrive_host import is_onedrive_enabled
from aird.plugins.onedrive_host import status as host_status
from aird.plugins.onedrive_host.sync_queue import kick_sync
from aird.plugins.onedrive_host.token_store import save_file_token, file_token_configured
from aird.plugins.onedrive_host.watcher import reload_watchers


def _require_admin(handler: BaseHandler) -> bool:
    if not is_onedrive_enabled():
        handler.set_status(403)
        handler.write({"error": "OneDrive host sync is disabled."})
        return False
    if not handler.is_admin_user():
        handler.set_status(403)
        handler.write({"error": "Admin only."})
        return False
    return True


class OneDriveHostStatusHandler(BaseHandler):
    @tornado.web.authenticated
    @require_db
    def get(self):
        if not _require_admin(self):
            return
        settings = get_settings(self.db_conn)
        run = od_db.get_run_status(self.db_conn, "__host__")
        self.write({
            "settings": settings,
            "file_token": file_token_configured(),
            "runtime": host_status.public_view(),
            "last_run": run,
        })


def _list_all_maps(conn) -> list[dict]:
    maps = od_db.list_maps(conn, "admin")
    if maps:
        return maps
    out = []
    for row in conn.execute(
        "SELECT id, username, local_path, remote_path, ignore_extra, updated_at "
        "FROM onedrive_folder_maps ORDER BY id"
    ).fetchall():
        out.append({
            "id": int(row[0]),
            "username": row[1],
            "local_path": row[2],
            "remote_path": row[3],
            "ignore_extra": row[4] or "",
            "updated_at": row[5],
        })
    return out


class OneDriveHostMapsHandler(BaseHandler, XSRFTokenMixin):
    @tornado.web.authenticated
    @require_db
    def get(self):
        if not _require_admin(self):
            return
        self.write({"maps": _list_all_maps(self.db_conn)})

    @tornado.web.authenticated
    @require_db
    def put(self):
        if not _require_admin(self):
            return
        self.check_xsrf_cookie()
        try:
            body = self.parse_json_body()
        except tornado.web.HTTPError:
            return
        username = str(body.get("username") or "admin").strip()
        local_path = str(body.get("local_path") or "").strip()
        remote_path = str(body.get("remote_path") or "").strip()
        ignore_extra = str(body.get("ignore_extra") or "")
        map_id = body.get("id")
        try:
            item = od_db.save_map(
                self.db_conn,
                username,
                map_id=int(map_id) if map_id else None,
                local_path=local_path,
                remote_path=remote_path,
                ignore_extra=ignore_extra,
            )
        except (ValueError, RuntimeError) as exc:
            self.set_status(400)
            self.write({"error": str(exc)})
            return
        reload_watchers(self.db_conn)
        self.write({"map": item})

    @tornado.web.authenticated
    @require_db
    def delete(self):
        if not _require_admin(self):
            return
        self.check_xsrf_cookie()
        map_id = self.get_argument("id", "")
        if not map_id:
            self.set_status(400)
            self.write({"error": "id required"})
            return
        od_db.delete_map(self.db_conn, "admin", int(map_id))
        reload_watchers(self.db_conn)
        self.write({"ok": True})


class OneDriveHostRulesHandler(BaseHandler, XSRFTokenMixin):
    @tornado.web.authenticated
    @require_db
    def get(self):
        if not _require_admin(self):
            return
        rules = od_db.get_rules(self.db_conn, "admin")
        rules["sync_paused"] = host_status.is_paused()
        self.write(rules)

    @tornado.web.authenticated
    @require_db
    def put(self):
        if not _require_admin(self):
            return
        self.check_xsrf_cookie()
        try:
            body = self.parse_json_body()
        except tornado.web.HTTPError:
            return
        if "sync_paused" in body:
            host_status.set_paused(bool(body.get("sync_paused")))
        rules = od_db.save_rules(
            self.db_conn,
            "admin",
            paths=body.get("paths"),
            include_untracked=bool(body.get("include_untracked", True)),
            include_aird_config=bool(body.get("include_aird_config", False)),
        )
        rules["sync_paused"] = host_status.is_paused()
        self.write(rules)


class OneDriveHostSyncNowHandler(BaseHandler, XSRFTokenMixin):
    @tornado.web.authenticated
    @require_db
    def post(self):
        if not _require_admin(self):
            return
        self.check_xsrf_cookie()
        kick_sync()
        self.write({"ok": True})


class OneDriveHostRetryHandler(BaseHandler, XSRFTokenMixin):
    @tornado.web.authenticated
    @require_db
    def post(self):
        if not _require_admin(self):
            return
        self.check_xsrf_cookie()
        paths = host_status.retry_failed()
        if paths:
            kick_sync()
        self.write({"ok": True, "queued": paths})


class OneDriveHostDeviceStartHandler(BaseHandler, XSRFTokenMixin):
    @tornado.web.authenticated
    @require_db
    def post(self):
        if not _require_admin(self):
            return
        self.check_xsrf_cookie()
        settings = get_settings(self.db_conn)
        try:
            flow = start_device_flow(settings["client_id"], settings["tenant"])
        except OneDriveAuthError as exc:
            self.set_status(400)
            self.write({"error": str(exc)})
            return
        save_device_pending(self.db_conn, flow)
        self.write({
            "user_code": flow.get("user_code"),
            "verification_uri": flow.get("verification_uri"),
            "expires_in": flow.get("expires_in"),
        })


class OneDriveHostDevicePollHandler(BaseHandler, XSRFTokenMixin):
    @tornado.web.authenticated
    @require_db
    def post(self):
        if not _require_admin(self):
            return
        self.check_xsrf_cookie()
        settings = get_settings(self.db_conn)
        pending = load_device_pending(self.db_conn)
        device_code = pending.get("device_code")
        if not device_code:
            self.set_status(400)
            self.write({"error": "No device login in progress"})
            return
        try:
            tokens = poll_device_flow(
                settings["client_id"], settings["tenant"], device_code
            )
        except OneDriveAuthError as exc:
            if exc.pending:
                self.write({"pending": True})
                return
            self.set_status(400)
            self.write({"error": str(exc)})
            return
        save_device_tokens(self.db_conn, tokens)
        save_file_token(tokens)
        reload_watchers(self.db_conn)
        self.write({"ok": True, "token_configured": True})


def register_routes(routes: list) -> None:
    routes.extend([
        (r"/api/onedrive-host/status", OneDriveHostStatusHandler),
        (r"/api/onedrive-host/maps", OneDriveHostMapsHandler),
        (r"/api/onedrive-host/rules", OneDriveHostRulesHandler),
        (r"/api/onedrive-host/sync", OneDriveHostSyncNowHandler),
        (r"/api/onedrive-host/retry", OneDriveHostRetryHandler),
        (r"/api/onedrive-host/device-start", OneDriveHostDeviceStartHandler),
        (r"/api/onedrive-host/device-poll", OneDriveHostDevicePollHandler),
    ])
