"""HTTP handlers for PWA manifest, service worker, and Web Push subscriptions."""

from __future__ import annotations

import json
import logging
import os
from typing import Any

import tornado.web

from aird.handlers.base_handler import (
    BaseHandler,
    XSRFTokenMixin,
    get_username_string_for_db,
)
from aird.handlers.constants import CONTENT_TYPE_JSON, DB_UNAVAILABLE_MSG, INVALID_JSON_MSG

logger = logging.getLogger(__name__)


def _static_js(*parts: str) -> str:
    return os.path.join(os.path.dirname(os.path.dirname(__file__)), "static", "js", *parts)


def _static_file(*parts: str) -> str:
    return os.path.join(os.path.dirname(os.path.dirname(__file__)), "static", *parts)


def _write_json(handler: BaseHandler, payload: dict[str, Any], *, status: int | None = None) -> None:
    if status is not None:
        handler.set_status(status)
    handler.set_header("Content-Type", CONTENT_TYPE_JSON)
    handler.write(json.dumps(payload))


class ManifestHandler(tornado.web.RequestHandler):
    """GET /manifest.webmanifest"""

    def get(self):
        path = _static_file("manifest.webmanifest")
        self.set_header("Content-Type", "application/manifest+json; charset=utf-8")
        self.set_header("Cache-Control", "no-cache")
        with open(path, "rb") as fh:
            self.write(fh.read())


class ServiceWorkerHandler(tornado.web.RequestHandler):
    """Serve the PWA service worker from site root (scope /)."""

    def get(self):
        sw_path = _static_js("sw.js")
        self.set_header("Content-Type", "application/javascript; charset=utf-8")
        self.set_header("Service-Worker-Allowed", "/")
        self.set_header("Cache-Control", "no-store, must-revalidate")
        self.set_header("Pragma", "no-cache")
        with open(sw_path, "rb") as fh:
            self.write(fh.read())


class PwaVapidPublicKeyHandler(BaseHandler):
    """GET /api/pwa/vapid-public-key"""

    def get(self):
        try:
            from aird.pwa.vapid import vapid_public_key

            key = vapid_public_key()
        except Exception as exc:
            logger.warning("VAPID public key unavailable: %s", exc)
            _write_json(self, {"error": "Push notifications are not configured."}, status=503)
            return
        _write_json(self, {"publicKey": key})


class PwaPushSubscribeHandler(XSRFTokenMixin, BaseHandler):
    """POST /api/pwa/push-subscribe — register a PushSubscription."""

    def post(self):
        username = get_username_string_for_db(self)
        if not username or username in ("token_user", "admin_token"):
            _write_json(self, {"error": "Login required"}, status=403)
            return
        if not self.db_conn:
            _write_json(self, {"error": DB_UNAVAILABLE_MSG}, status=503)
            return
        try:
            body = json.loads(self.request.body or b"{}")
        except json.JSONDecodeError:
            _write_json(self, {"error": INVALID_JSON_MSG}, status=400)
            return
        endpoint = (body.get("endpoint") or "").strip()
        keys = body.get("keys") or {}
        p256dh = (keys.get("p256dh") or "").strip()
        auth = (keys.get("auth") or "").strip()
        if not endpoint or not p256dh or not auth:
            _write_json(self, {"error": "Invalid subscription"}, status=400)
            return
        from aird.pwa import store as push_store

        push_store.upsert_subscription(
            self.db_conn,
            username=username,
            endpoint=endpoint,
            p256dh=p256dh,
            auth=auth,
            user_agent=self.request.headers.get("User-Agent"),
        )
        _write_json(self, {"ok": True})


class PwaPushUnsubscribeHandler(XSRFTokenMixin, BaseHandler):
    """POST /api/pwa/push-unsubscribe"""

    def post(self):
        username = get_username_string_for_db(self)
        if not username or username in ("token_user", "admin_token"):
            _write_json(self, {"error": "Login required"}, status=403)
            return
        if not self.db_conn:
            _write_json(self, {"error": DB_UNAVAILABLE_MSG}, status=503)
            return
        try:
            body = json.loads(self.request.body or b"{}")
        except json.JSONDecodeError:
            _write_json(self, {"error": INVALID_JSON_MSG}, status=400)
            return
        endpoint = (body.get("endpoint") or "").strip()
        if not endpoint:
            _write_json(self, {"error": "endpoint required"}, status=400)
            return
        from aird.pwa import store as push_store

        push_store.delete_subscription(self.db_conn, endpoint=endpoint, username=username)
        _write_json(self, {"ok": True})
