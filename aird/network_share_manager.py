"""Manages embedded SMB (pysmbserver, SMB1/2) and WebDAV (WsgiDAV+cheroot, Class 2) servers."""

import logging
import os
import threading
from typing import Any

logger = logging.getLogger(__name__)

_SMB_AVAILABLE = False
_WEBDAV_AVAILABLE = False

try:
    from pysmbserver.smbserver import SimpleSMBServer as PySMBServer

    _SMB_AVAILABLE = True
except Exception:
    pass

try:
    from wsgidav.wsgidav_app import WsgiDAVApp
    from cheroot import wsgi as cheroot_wsgi

    _WEBDAV_AVAILABLE = True
except Exception:
    pass


_DEFAULT_BIND_ADDRESS = "127.0.0.1"


class NetworkShareManager:
    """Start / stop pure-Python SMB and WebDAV servers in daemon threads."""

    def __init__(self, bind_address: str = _DEFAULT_BIND_ADDRESS) -> None:
        self._servers: dict[str, dict[str, Any]] = {}
        self._lock = threading.Lock()
        self._bind_address = bind_address

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def start_share(self, share: dict) -> bool:
        """Start a server for *share* (dict with id, name, folder_path, protocol, port, username, password, read_only)."""
        sid = share["id"]
        with self._lock:
            existing = self._servers.get(sid)
            if existing is not None:
                if existing["thread"].is_alive():
                    logger.warning("Share %s is already running", sid)
                    return False
                # Thread crashed or finished -- clean up stale entry so we can restart
                self._servers.pop(sid, None)

        folder = share["folder_path"]
        if not os.path.isdir(folder):
            logger.error("Share folder does not exist: %s", folder)
            return False

        protocol = share.get("protocol", "webdav").lower()
        if protocol == "smb":
            return self._start_smb(share)
        return self._start_webdav(share)

    def stop_share(self, share_id: str) -> bool:
        with self._lock:
            entry = self._servers.pop(share_id, None)
        if entry is None:
            return False
        self._shutdown_server(entry)
        logger.info("Stopped network share %s", share_id)
        return True

    def stop_all(self) -> None:
        with self._lock:
            entries = list(self._servers.values())
            self._servers.clear()
        for entry in entries:
            self._shutdown_server(entry)
        logger.info("All network shares stopped")

    def is_running(self, share_id: str) -> bool:
        with self._lock:
            entry = self._servers.get(share_id)
        if entry is None:
            return False
        return entry["thread"].is_alive()

    def get_status(self, share_id: str) -> dict:
        running = self.is_running(share_id)
        with self._lock:
            entry = self._servers.get(share_id)
        if entry is None:
            return {"running": False}
        return {
            "running": running,
            "protocol": entry["share"].get("protocol"),
            "port": entry["share"].get("port"),
        }

    # ------------------------------------------------------------------
    # SMB
    # ------------------------------------------------------------------

    def _start_smb(self, share: dict) -> bool:
        if not _SMB_AVAILABLE:
            logger.error("pysmbserver is not installed – cannot start SMB share")
            return False

        sid = share["id"]
        port = int(share["port"])
        share_name = share["name"].upper()
        folder = share["folder_path"]
        username = share["username"]
        password = share["password"]
        read_only = "yes" if share.get("read_only") else "no"

        def _run() -> None:
            try:
                server = PySMBServer(listenAddress=self._bind_address, listenPort=port)
                server.addShare(share_name, folder, readOnly=read_only)
                server.addCredential(username, password=password)
                server.setSMB2Support(True)
                server.setSMBChallenge("")
                with self._lock:
                    if sid in self._servers:
                        self._servers[sid]["server"] = server
                logger.info(
                    "SMB share '%s' listening on port %d (folder: %s)",
                    share_name,
                    port,
                    folder,
                )
                server.start()
            except Exception:
                logger.exception("SMB share '%s' crashed", share_name)

        t = threading.Thread(target=_run, name=f"smb-{sid}", daemon=True)
        with self._lock:
            self._servers[sid] = {"thread": t, "server": None, "share": share}
        t.start()
        return True

    # ------------------------------------------------------------------
    # WebDAV
    # ------------------------------------------------------------------

    def _start_webdav(self, share: dict) -> bool:
        if not _WEBDAV_AVAILABLE:
            logger.error("wsgidav/cheroot is not installed – cannot start WebDAV share")
            return False

        sid = share["id"]
        port = int(share["port"])
        folder = share["folder_path"]
        username = share["username"]
        password = share["password"]
        read_only = bool(share.get("read_only"))

        def _run() -> None:
            try:
                dav_config: dict[str, Any] = {
                    "host": self._bind_address,
                    "port": port,
                    "provider_mapping": {"/": folder},
                    "simple_dc": {
                        "user_mapping": {
                            "*": {username: {"password": password}},
                        },
                    },
                    "fs_dav_provider": {
                        "readonly": read_only,
                    },
                    "http_authenticator": {
                        "domain_controller": None,
                        "accept_basic": True,
                        "accept_digest": True,
                        "default_to_digest": True,
                    },
                    "lock_storage": True,
                    "property_manager": True,
                    "hotfixes": {
                        "emulate_win32_lastmod": True,
                        "re_encode_path_info": True,
                        "unquote_path_info": False,
                        "win_accept_anonymous_options": True,
                    },
                    "dir_browser": {
                        "enable": True,
                        "response_trailer": "",
                        "show_user": True,
                        "show_logout": True,
                        "davmount": False,
                        "ms_sharepoint_support": True,
                        "libre_office_support": True,
                    },
                    "verbose": 1,
                }
                app = WsgiDAVApp(dav_config)
                srv = cheroot_wsgi.Server((self._bind_address, port), app)
                with self._lock:
                    if sid in self._servers:
                        self._servers[sid]["server"] = srv
                logger.info(
                    "WebDAV share '%s' listening on port %d (folder: %s)",
                    share["name"],
                    port,
                    folder,
                )
                srv.start()
            except Exception:
                logger.exception("WebDAV share '%s' crashed", share["name"])

        t = threading.Thread(target=_run, name=f"webdav-{sid}", daemon=True)
        with self._lock:
            self._servers[sid] = {"thread": t, "server": None, "share": share}
        t.start()
        return True

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _shutdown_server(entry: dict) -> None:
        server = entry.get("server")
        if server is None:
            return
        try:
            if hasattr(server, "stop"):
                server.stop()
        except Exception:
            logger.debug("Error stopping server for share", exc_info=True)
