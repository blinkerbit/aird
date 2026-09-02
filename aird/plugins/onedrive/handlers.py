"""HTTP handlers for OneDrive backup/restore."""

from __future__ import annotations

import io
import logging

import tornado.ioloop
import tornado.web

from aird.cloud import CloudProviderError, OneDriveProvider
from aird.constants.input_limits import ONEDRIVE_TOKEN_MAX_LEN
from aird.handlers.base_handler import (
    BaseHandler,
    XSRFTokenMixin,
    get_username_string_for_db,
    require_db,
    resolve_handler_rel,
)
from aird.plugins.access import PLUGIN_ONEDRIVE, user_may_use_plugin
from aird.plugins.onedrive import db as od_db
from aird.plugins.onedrive import is_onedrive_enabled
from aird.plugins.onedrive.backup import (
    BACKUP_FOLDER,
    backup_filename,
    build_zip_bytes,
    extract_zip_bytes,
)
from aird.plugins.onedrive.settings import get_settings, resolve_access_token, snapshots_remote_path
from aird.plugins.onedrive.token import (
    delete_user_token,
    load_user_token,
    save_user_token,
    token_configured,
)

logger = logging.getLogger(__name__)
_TOKEN_ONLY = frozenset({"token_user", "admin_token"})


def _require_onedrive(handler: BaseHandler) -> bool:
    if not is_onedrive_enabled():
        handler.set_status(403)
        handler.write({"error": "OneDrive backup is disabled."})
        return False
    username = get_username_string_for_db(handler)
    if not username or username in _TOKEN_ONLY:
        handler.set_status(403)
        handler.write({"error": "OneDrive backup requires a logged-in user account."})
        return False
    if not user_may_use_plugin(PLUGIN_ONEDRIVE, username, handler.db_conn):
        handler.set_status(403)
        handler.write({"error": "OneDrive backup is not assigned to this account."})
        return False
    return True


def _username(handler: BaseHandler) -> str:
    return get_username_string_for_db(handler) or ""


def _provider(username: str, conn=None) -> OneDriveProvider | None:
    token = load_user_token(username, conn=conn)
    if not token:
        return None
    return OneDriveProvider(token)


def _schedule_user_sync(username: str) -> None:
    name = (username or "").strip()
    if not name:
        return
    loop = tornado.ioloop.IOLoop.current()

    def run() -> None:
        try:
            import aird.constants as constants
            from aird.plugins.onedrive.sync import sync_user

            sync_user(getattr(constants, "DB_CONN", None), name)
        except Exception:
            logger.exception("OneDrive background sync failed for %s", name)

    loop.call_later(2, lambda: loop.run_in_executor(None, run))


def _list_zip_folder(provider: OneDriveProvider, path: str) -> list[dict]:
    item = provider.get_item_by_path(path)
    if item is None or not item.is_dir:
        return []
    folder_id = item.id if item.id and item.id != "root" else None
    return [
        {"id": f.id, "name": f.name, "size": f.size, "modified": f.modified}
        for f in provider.list_files(folder_id)
        if not f.is_dir and str(f.name).endswith(".zip")
    ]


class OneDrivePageHandler(BaseHandler):
    @tornado.web.authenticated
    @require_db
    def get(self):
        if not _require_onedrive(self):
            return
        name = _username(self)
        rules = od_db.get_rules(self.db_conn, name)
        settings = get_settings(self.db_conn)
        self.render(
            "onedrive.html",
            token_configured=token_configured(name, conn=self.db_conn),
            rules=rules,
            maps=od_db.list_maps(self.db_conn, name),
            sync_status=od_db.get_run_status(self.db_conn, name),
            root_path=settings["root_path"],
            sync_interval_minutes=settings["sync_interval_minutes"],
            username=name,
        )


class OneDriveTokenHandler(BaseHandler, XSRFTokenMixin):
    @tornado.web.authenticated
    @require_db
    def put(self):
        if not _require_onedrive(self):
            return
        self.check_xsrf_cookie()
        try:
            body = self.parse_json_body()
        except tornado.web.HTTPError:
            return
        token = str(body.get("token") or "").strip()
        if not token or len(token) > ONEDRIVE_TOKEN_MAX_LEN:
            self.set_status(400)
            self.write({"error": "Invalid OneDrive token."})
            return
        save_user_token(_username(self), token, conn=self.db_conn)
        self.write({"ok": True, "token_configured": True})

    @tornado.web.authenticated
    @require_db
    def delete(self):
        if not _require_onedrive(self):
            return
        self.check_xsrf_cookie()
        delete_user_token(_username(self), conn=self.db_conn)
        self.write({"ok": True, "token_configured": False})


class OneDriveRulesHandler(BaseHandler, XSRFTokenMixin):
    @tornado.web.authenticated
    @require_db
    def get(self):
        if not _require_onedrive(self):
            return
        self.write(od_db.get_rules(self.db_conn, _username(self)))

    @tornado.web.authenticated
    @require_db
    def put(self):
        if not _require_onedrive(self):
            return
        self.check_xsrf_cookie()
        try:
            body = self.parse_json_body()
        except tornado.web.HTTPError:
            return
        current = od_db.get_rules(self.db_conn, _username(self))
        rules = od_db.save_rules(
            self.db_conn,
            _username(self),
            paths=current["paths"],
            include_untracked=False,
            include_aird_config=bool(body.get("include_aird_config", False)),
        )
        _schedule_user_sync(_username(self))
        self.write(rules)


class OneDriveMapsHandler(BaseHandler, XSRFTokenMixin):
    @tornado.web.authenticated
    @require_db
    def get(self):
        if not _require_onedrive(self):
            return
        self.write({"maps": od_db.list_maps(self.db_conn, _username(self))})

    @tornado.web.authenticated
    @require_db
    def post(self):
        if not _require_onedrive(self):
            return
        self.check_xsrf_cookie()
        try:
            body = self.parse_json_body()
        except tornado.web.HTTPError:
            return
        try:
            item = od_db.save_map(
                self.db_conn,
                _username(self),
                map_id=int(body["id"]) if body.get("id") else None,
                local_path=str(body.get("local_path") or ""),
                remote_path=str(body.get("remote_path") or body.get("local_path") or ""),
                ignore_extra=str(body.get("ignore_extra") or ""),
            )
        except (TypeError, ValueError) as exc:
            self.set_status(400)
            self.write({"error": str(exc)})
            return
        _schedule_user_sync(_username(self))
        self.write({"ok": True, "map": item})

    @tornado.web.authenticated
    @require_db
    def delete(self):
        if not _require_onedrive(self):
            return
        self.check_xsrf_cookie()
        try:
            body = self.parse_json_body()
        except tornado.web.HTTPError:
            return
        try:
            map_id = int(body.get("id") or 0)
        except (TypeError, ValueError):
            map_id = 0
        if map_id <= 0:
            self.set_status(400)
            self.write({"error": "Map id is required."})
            return
        od_db.delete_map(self.db_conn, _username(self), map_id)
        self.write({"ok": True})


class OneDriveStatusHandler(BaseHandler):
    @tornado.web.authenticated
    @require_db
    def get(self):
        if not _require_onedrive(self):
            return
        name = _username(self)
        settings = get_settings(self.db_conn)
        self.write(
            {
                "rules": od_db.get_rules(self.db_conn, name),
                "maps": od_db.list_maps(self.db_conn, name),
                "sync": od_db.get_run_status(self.db_conn, name),
                "root_path": settings["root_path"],
                "sync_interval_minutes": settings["sync_interval_minutes"],
                "token_configured": token_configured(name, conn=self.db_conn),
            }
        )


class OneDriveSyncHandler(BaseHandler, XSRFTokenMixin):
    @tornado.web.authenticated
    @require_db
    def post(self):
        if not _require_onedrive(self):
            return
        self.check_xsrf_cookie()
        from aird.plugins.onedrive.sync import sync_user

        result = sync_user(self.db_conn, _username(self))
        if not result.get("ok") and result.get("error"):
            status = 400 if "token" in str(result["error"]).lower() or "assigned" in str(result["error"]).lower() else 502
            self.set_status(status)
        self.write(result)


class OneDriveBackupsHandler(BaseHandler):
    @tornado.web.authenticated
    @require_db
    def get(self):
        if not _require_onedrive(self):
            return
        name = _username(self)
        provider = _provider(name, conn=self.db_conn)
        if provider is None:
            self.set_status(400)
            self.write({"error": "OneDrive token is not configured."})
            return
        root = get_settings(self.db_conn)["root_path"]
        try:
            files = _list_zip_folder(provider, snapshots_remote_path(root, name))
            seen = {item["id"] for item in files}
            for item in _list_zip_folder(provider, BACKUP_FOLDER):
                if item["id"] not in seen:
                    files.append(item)
        except CloudProviderError as exc:
            self.set_status(502)
            self.write({"error": str(exc)})
            return
        self.write({"backups": files})


class OneDriveBackupHandler(BaseHandler, XSRFTokenMixin):
    @tornado.web.authenticated
    @require_db
    def post(self):
        if not _require_onedrive(self):
            return
        self.check_xsrf_cookie()
        name = _username(self)
        provider = _provider(name, conn=self.db_conn)
        if provider is None:
            self.set_status(400)
            self.write({"error": "OneDrive token is not configured."})
            return
        from aird.plugins.onedrive.sync import _entries_for_user

        entries = _entries_for_user(self.db_conn, name)
        if not entries:
            self.set_status(400)
            self.write({"error": "Nothing to back up. Map a folder or include .aird config."})
            return
        blob = build_zip_bytes(entries)
        filename = backup_filename(name)
        root = get_settings(self.db_conn)["root_path"]
        try:
            folder = provider.ensure_folder_path(snapshots_remote_path(root, name))
            uploaded = provider.upload_file(
                io.BytesIO(blob),
                name=filename,
                parent_id=None if folder.id in ("", "root") else folder.id,
                size=len(blob),
                content_type="application/zip",
            )
        except CloudProviderError as exc:
            logger.exception("OneDrive backup upload failed")
            self.set_status(502)
            self.write({"error": str(exc)})
            return
        self.write(
            {
                "ok": True,
                "file_count": len(entries),
                "bytes": len(blob),
                "name": uploaded.name,
                "id": uploaded.id,
            }
        )


class OneDriveRestoreHandler(BaseHandler, XSRFTokenMixin):
    @tornado.web.authenticated
    @require_db
    def post(self):
        if not _require_onedrive(self):
            return
        self.check_xsrf_cookie()
        try:
            body = self.parse_json_body()
        except tornado.web.HTTPError:
            return
        item_id = str(body.get("id") or "").strip()
        if not item_id:
            self.set_status(400)
            self.write({"error": "Backup id is required."})
            return
        name = _username(self)
        provider = _provider(name, conn=self.db_conn)
        if provider is None:
            self.set_status(400)
            self.write({"error": "OneDrive token is not configured."})
            return
        try:
            download = provider.download_file(item_id)
            chunks = b"".join(download.iter_chunks())
            download.close()
            restored = extract_zip_bytes(chunks, name)
        except CloudProviderError as exc:
            self.set_status(502)
            self.write({"error": str(exc)})
            return
        except (OSError, ValueError) as exc:
            self.set_status(400)
            self.write({"error": str(exc)})
            return
        self.write({"ok": True, "restored": len(restored)})


def _require_admin_server_onedrive(handler: BaseHandler) -> bool:
    if not is_onedrive_enabled():
        handler.set_status(403)
        handler.write({"error": "OneDrive backup is disabled."})
        return False
    if not handler.is_admin_user():
        handler.set_status(403)
        handler.write({"error": "Server OneDrive save is limited to administrators."})
        return False
    if not resolve_access_token(handler.db_conn):
        handler.set_status(400)
        handler.write(
            {
                "error": "Server OneDrive is not configured. Set up host backup auth under Admin → Plugins → OneDrive host backup.",
            }
        )
        return False
    return True


def _server_provider(conn) -> OneDriveProvider | None:
    token = resolve_access_token(conn)
    if not token:
        return None
    return OneDriveProvider(token)


class OneDriveBrowseSaveHandler(BaseHandler, XSRFTokenMixin):
    @tornado.web.authenticated
    @require_db
    def post(self):
        if not _require_admin_server_onedrive(self):
            return
        self.check_xsrf_cookie()
        try:
            body = self.parse_json_body()
        except tornado.web.HTTPError:
            return
        items = body.get("items") or []
        if not isinstance(items, list) or not items:
            self.set_status(400)
            self.write({"error": "Select at least one file or folder."})
            return
        provider = _server_provider(self.db_conn)
        if provider is None:
            self.set_status(400)
            self.write({"error": "Server OneDrive token is not configured."})
            return

        def resolve_local(rel: str) -> str:
            abspath, _confine = resolve_handler_rel(self, rel)
            if not abspath or not os.path.exists(abspath):
                raise FileNotFoundError(rel)
            return abspath

        from aird.plugins.onedrive.browse_save import save_browse_items

        remote_root = str(body.get("remote_root") or "").strip() or None
        try:
            result = save_browse_items(
                provider,
                items=items,
                resolve_local=resolve_local,
                remote_root=remote_root,
                settings_conn=self.db_conn,
            )
        except FileNotFoundError as exc:
            self.set_status(404)
            self.write({"error": f"Not found: {exc}"})
            return
        except (ValueError, OSError) as exc:
            self.set_status(400)
            self.write({"error": str(exc)})
            return
        except CloudProviderError as exc:
            logger.exception("OneDrive browse save failed")
            self.set_status(502)
            self.write({"error": str(exc)})
            return
        self.write(result)
