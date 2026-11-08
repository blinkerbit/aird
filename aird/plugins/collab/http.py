"""Shared HTTP helpers for collab plugins."""

from __future__ import annotations

import json

import tornado.web

from aird.handlers.base_handler import BaseHandler, get_username_string_for_db


def parse_json(handler: BaseHandler) -> dict:
    try:
        body = json.loads(handler.request.body or b"{}")
    except json.JSONDecodeError:
        raise tornado.web.HTTPError(400, reason="Invalid JSON") from None
    if not isinstance(body, dict):
        raise tornado.web.HTTPError(400, reason="Invalid JSON")
    return body


def require_username(handler: BaseHandler) -> str:
    name = get_username_string_for_db(handler)
    if not name:
        raise tornado.web.HTTPError(403, reason="Forbidden")
    return name
