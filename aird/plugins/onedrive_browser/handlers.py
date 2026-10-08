"""Public config + OAuth callback page. No tokens accepted or stored."""

from __future__ import annotations

import tornado.web

from aird.handlers.base_handler import (
    BaseHandler,
    get_username_string_for_db,
    require_db,
)
from aird.plugins.access import PLUGIN_ONEDRIVE_BROWSER, user_may_use_plugin
from aird.plugins.onedrive_browser import is_onedrive_browser_enabled
from aird.plugins.onedrive_browser.settings import public_config

_TOKEN_ONLY = frozenset({"token_user", "admin_token"})


def _allowed(handler: BaseHandler) -> bool:
    if not is_onedrive_browser_enabled():
        return False
    username = get_username_string_for_db(handler)
    if not username or username in _TOKEN_ONLY:
        return False
    return user_may_use_plugin(PLUGIN_ONEDRIVE_BROWSER, username, handler.db_conn)


class OneDriveBrowserConfigHandler(BaseHandler):
    @tornado.web.authenticated
    @require_db
    def get(self):
        if not _allowed(self):
            self.set_status(403)
            self.write({"error": "OneDrive browser is disabled.", "enabled": False})
            return
        self.write({"enabled": True, **public_config(self.db_conn)})


class OneDriveBrowserCallbackHandler(BaseHandler):
    @tornado.web.authenticated
    @require_db
    def get(self):
        if not _allowed(self):
            self.set_status(403)
            self.write("OneDrive browser is disabled.")
            return
        cfg = public_config(self.db_conn)
        self.render("onedrive_browser_callback.html", od_browser=cfg)
