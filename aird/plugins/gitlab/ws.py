"""WebSocket for live file comments."""

from __future__ import annotations

import json
import logging

import tornado.websocket

from aird.core.security import is_valid_websocket_origin
from aird.handlers.base_handler import ManagedWebSocketMixin, authenticate_handler
from aird.plugins.gitlab import is_gitlab_enabled
from aird.plugins.gitlab.acl import resolve_access
from aird.plugins.gitlab.hub import get_comment_hub
from aird.utils.util import WebSocketConnectionManager

logger = logging.getLogger(__name__)


class GitlabCommentsWebSocketHandler(ManagedWebSocketMixin, tornado.websocket.WebSocketHandler):
    connection_manager = WebSocketConnectionManager(
        "file_comments", default_max_connections=100, default_idle_timeout=600
    )

    def __init__(self, application, request, **kwargs):
        super().__init__(application, request, **kwargs)
        self._owner = ""
        self._path = ""
        self._user = None

    @property
    def db_conn(self):
        return self.application.settings.get("db_conn")

    def get_current_user(self):
        return self._user

    def check_origin(self, origin: str) -> bool:
        return is_valid_websocket_origin(self, origin)

    def open(self):
        if not is_gitlab_enabled():
            self.close(code=1008, reason="GitLab disabled")
            return
        user = authenticate_handler(self)
        if not user:
            self.close(code=1008, reason="Authentication required")
            return
        from aird.plugins.access import PLUGIN_GITLAB, user_may_use_plugin

        username = user.get("username") if isinstance(user, dict) else None
        conn = self.application.settings.get("db_conn")
        if not user_may_use_plugin(PLUGIN_GITLAB, username, conn):
            self.close(code=1008, reason="GitLab not assigned")
            return
        self._user = user
        if not self.register_connection():
            return
        path = self.get_argument("path", "") or ""
        share_id = (self.get_argument("share_id", "") or "").strip() or None
        try:
            access = resolve_access(self, path=path, share_id=share_id)
        except ValueError:
            self.close(code=1008, reason="Invalid path")
            return
        if not access or not access.can_read:
            self.close(code=1008, reason="Access denied")
            return
        self._owner = access.owner_username
        self._path = access.rel_path
        get_comment_hub().add(self._owner, self._path, self)
        self.write_message(json.dumps({"type": "comments_ready", "path": self._path}))

    def on_close(self):
        if self._owner:
            get_comment_hub().remove(self._owner, self._path, self)
        super().on_close()

    def on_message(self, message):
        if self.reject_oversized_ws_message(message):
            return
