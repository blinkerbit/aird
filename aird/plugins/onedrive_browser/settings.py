"""Public Azure SPA settings for OneDrive browser SSO (not a secret)."""

from __future__ import annotations

import json
import re
import sqlite3

from aird.db.config import load_server_config, save_server_config

CONFIG_KEY = "onedrive_browser"
_CLIENT_RE = re.compile(r"^[0-9a-fA-F-]{8,80}$")


def _tenant(raw: str | None) -> str:
    value = (raw or "common").strip() or "common"
    if value.lower() in {"common", "consumers", "organizations"}:
        return value.lower()
    if _CLIENT_RE.match(value):
        return value
    return "common"


def _client_id(raw: str | None) -> str:
    value = (raw or "").strip()
    if value and _CLIENT_RE.match(value):
        return value
    return ""


def get_settings(conn: sqlite3.Connection | None) -> dict:
    defaults = {"client_id": "", "tenant": "common"}
    if conn is None:
        return defaults
    raw = load_server_config(conn).get(CONFIG_KEY)
    if not raw:
        return defaults
    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        return defaults
    if not isinstance(data, dict):
        return defaults
    return {
        "client_id": _client_id(str(data.get("client_id") or "")),
        "tenant": _tenant(str(data.get("tenant") or "common")),
    }


def save_settings(conn: sqlite3.Connection, *, client_id: str = "", tenant: str = "common") -> dict:
    current = {
        "client_id": _client_id(client_id),
        "tenant": _tenant(tenant),
    }
    save_server_config(conn, {CONFIG_KEY: json.dumps(current)}, bump_revision=False)
    return current


def public_config(conn: sqlite3.Connection | None) -> dict:
    settings = get_settings(conn)
    client_id = settings["client_id"]
    tenant = settings["tenant"]
    if not client_id and conn is not None:
        from aird.plugins.onedrive.settings import get_settings as host_settings

        host = host_settings(conn)
        if host.get("client_id"):
            client_id = host["client_id"]
            tenant = host.get("tenant") or tenant
    return {
        "client_id": client_id,
        "tenant": tenant,
        "configured": bool(client_id),
    }
