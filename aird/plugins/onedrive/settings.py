"""Admin-controlled OneDrive backup settings (paths, sync, Graph auth)."""

from __future__ import annotations

import json
import logging
import os
import re
import sqlite3
import time

from aird.cloud import split_drive_path
from aird.constants.input_limits import ONEDRIVE_TOKEN_MAX_LEN
from aird.core.secret_storage import decrypt_secret, encrypt_secret
from aird.db.config import load_server_config, save_server_config
from aird.plugins.onedrive.auth import OneDriveAuthError, refresh_access_token

logger = logging.getLogger(__name__)

CONFIG_KEY = "onedrive_plugin"
TOKEN_CONFIG_KEY = "onedrive_plugin_token"
DEVICE_PENDING_KEY = "onedrive_plugin_device_pending"
DEFAULT_ROOT = "Aird"
DEFAULT_INTERVAL_MINUTES = 10
MIN_INTERVAL_MINUTES = 5
MAX_INTERVAL_MINUTES = 1440
MAX_ROOT_CHARS = 512
AUTH_SOURCE_DEVICE = "device"
AUTH_SOURCE_ENV = "env"
AUTH_SOURCE_STORED = "stored"
AUTH_SOURCES = frozenset({AUTH_SOURCE_DEVICE, AUTH_SOURCE_ENV, AUTH_SOURCE_STORED})
DEFAULT_AUTH_SOURCE = AUTH_SOURCE_DEVICE
DEFAULT_ENV_VAR = "ONEDRIVE_TOKEN"
_CLIENT_RE = re.compile(r"^[0-9a-fA-F-]{8,80}$")
_ENV_VAR_RE = re.compile(r"^[A-Za-z_]\w{0,63}$")
_FALLBACK_ENV_VARS = ("ONEDRIVE_TOKEN", "GRAPH_ACCESS_TOKEN", "MS_GRAPH_TOKEN")


def _safe_user_folder(username: str) -> str:
    safe = "".join(c for c in (username or "") if c.isalnum() or c in "-_.")
    return (safe or "user")[:64]


def normalize_root_path(raw: str | None) -> str:
    parts = split_drive_path(raw or "")
    if not parts:
        return DEFAULT_ROOT
    joined = "/".join(parts)
    if len(joined) > MAX_ROOT_CHARS:
        return DEFAULT_ROOT
    return joined


def normalize_interval_minutes(raw) -> int:
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return DEFAULT_INTERVAL_MINUTES
    return max(MIN_INTERVAL_MINUTES, min(MAX_INTERVAL_MINUTES, value))


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


def _auth_source(raw: str | None) -> str:
    value = (raw or DEFAULT_AUTH_SOURCE).strip().lower()
    return value if value in AUTH_SOURCES else DEFAULT_AUTH_SOURCE


def _env_var(raw: str | None) -> str:
    value = (raw or DEFAULT_ENV_VAR).strip()
    if value and _ENV_VAR_RE.match(value):
        return value
    return DEFAULT_ENV_VAR


def _load_json_config(conn: sqlite3.Connection | None) -> dict:
    if conn is None:
        return {}
    raw = load_server_config(conn).get(CONFIG_KEY)
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _base_settings(data: dict | None) -> dict:
    parsed = data if isinstance(data, dict) else {}
    return {
        "root_path": normalize_root_path(str(parsed.get("root_path") or DEFAULT_ROOT)),
        "sync_interval_minutes": normalize_interval_minutes(
            parsed.get("sync_interval_minutes", DEFAULT_INTERVAL_MINUTES)
        ),
        "auth_source": _auth_source(str(parsed.get("auth_source") or DEFAULT_AUTH_SOURCE)),
        "client_id": _client_id(str(parsed.get("client_id") or "")),
        "tenant": _tenant(str(parsed.get("tenant") or "common")),
        "token_env_var": _env_var(str(parsed.get("token_env_var") or DEFAULT_ENV_VAR)),
    }


def _load_token_blob(conn: sqlite3.Connection | None) -> dict:
    if conn is None:
        return {}
    raw = (load_server_config(conn).get(TOKEN_CONFIG_KEY) or "").strip()
    if not raw:
        return {}
    try:
        text = decrypt_secret(raw)
        data = json.loads(text)
    except (TypeError, ValueError):
        return {"access_token": text} if text else {}
    return data if isinstance(data, dict) else {}


def save_token_blob(conn: sqlite3.Connection, payload: dict) -> None:
    encoded = json.dumps(payload)
    save_server_config(
        conn, {TOKEN_CONFIG_KEY: encrypt_secret(encoded)}, bump_revision=False
    )


def clear_token_blob(conn: sqlite3.Connection) -> None:
    save_server_config(conn, {TOKEN_CONFIG_KEY: ""}, bump_revision=False)


def save_stored_access_token(conn: sqlite3.Connection, token: str) -> None:
    value = (token or "").strip()
    if not value or len(value) > ONEDRIVE_TOKEN_MAX_LEN:
        raise ValueError("OneDrive token is required")
    save_token_blob(
        conn,
        {"access_token": value, "refresh_token": "", "expires_at": int(time.time()) + 3600},
    )


def save_device_tokens(conn: sqlite3.Connection, payload: dict) -> None:
    if not payload.get("access_token"):
        raise ValueError("Access token missing")
    save_token_blob(conn, payload)
    clear_device_pending(conn)


def load_device_pending(conn: sqlite3.Connection | None) -> dict:
    if conn is None:
        return {}
    raw = load_server_config(conn).get(DEVICE_PENDING_KEY)
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def save_device_pending(conn: sqlite3.Connection, payload: dict) -> None:
    save_server_config(
        conn, {DEVICE_PENDING_KEY: json.dumps(payload)}, bump_revision=False
    )


def clear_device_pending(conn: sqlite3.Connection) -> None:
    save_server_config(conn, {DEVICE_PENDING_KEY: ""}, bump_revision=False)


def _env_token(var_name: str) -> str | None:
    primary = (os.environ.get(var_name) or "").strip()
    if primary:
        return primary
    for name in _FALLBACK_ENV_VARS:
        if name == var_name:
            continue
        value = (os.environ.get(name) or "").strip()
        if value:
            return value
    return None


def _device_access_token(conn: sqlite3.Connection, settings: dict) -> str | None:
    blob = _load_token_blob(conn)
    access = (blob.get("access_token") or "").strip()
    refresh = (blob.get("refresh_token") or "").strip()
    expires_at = int(blob.get("expires_at") or 0)
    if access and expires_at > int(time.time()) + 120:
        return access
    if not refresh:
        return access or None
    client_id = settings.get("client_id") or ""
    tenant = settings.get("tenant") or "common"
    if not client_id:
        return None
    try:
        refreshed = refresh_access_token(client_id, tenant, refresh)
    except OneDriveAuthError:
        logger.warning("OneDrive token refresh failed", exc_info=True)
        return None
    if refreshed.get("refresh_token"):
        save_device_tokens(conn, refreshed)
    else:
        refreshed["refresh_token"] = refresh
        save_device_tokens(conn, refreshed)
    return refreshed.get("access_token")


def resolve_access_token(conn: sqlite3.Connection | None) -> str | None:
    settings = _base_settings(_load_json_config(conn))
    source = settings["auth_source"]
    if source == AUTH_SOURCE_ENV:
        return _env_token(settings["token_env_var"])
    if source == AUTH_SOURCE_STORED:
        blob = _load_token_blob(conn)
        return (blob.get("access_token") or "").strip() or None
    if source == AUTH_SOURCE_DEVICE:
        if conn is None:
            return None
        return _device_access_token(conn, settings)
    return None


def _auth_token_status(cfg: dict, blob: dict, conn: sqlite3.Connection | None) -> tuple[bool, str]:
    source = cfg["auth_source"]
    if source == AUTH_SOURCE_ENV:
        var = cfg["token_env_var"]
        configured = bool(_env_token(var))
        detail = f"{var} is set" if configured else f"{var} not set in environment"
        return configured, detail
    if source == AUTH_SOURCE_STORED:
        configured = bool((blob.get("access_token") or "").strip())
        return configured, "Stored in Aird" if configured else "No token saved"
    configured = bool((blob.get("refresh_token") or "").strip()) or bool(resolve_access_token(conn))
    if configured:
        return True, "Device login complete"
    if cfg.get("client_id"):
        return False, "Run device login"
    return False, "Azure client ID required"


def auth_status(conn: sqlite3.Connection | None, settings: dict | None = None) -> dict:
    cfg = settings or _base_settings(_load_json_config(conn))
    blob = _load_token_blob(conn)
    configured, detail = _auth_token_status(cfg, blob, conn)
    pending = load_device_pending(conn)
    return {
        "auth_source": cfg["auth_source"],
        "client_id": cfg["client_id"],
        "tenant": cfg["tenant"],
        "token_env_var": cfg["token_env_var"],
        "token_configured": configured,
        "token_status_detail": detail,
        "stored_token_configured": bool((blob.get("access_token") or "").strip()),
        "device_pending": bool(pending.get("device_code")),
        "device_user_code": pending.get("user_code") or "",
        "device_verification_uri": pending.get("verification_uri") or "",
    }


def get_settings(conn: sqlite3.Connection | None) -> dict:
    cfg = _base_settings(_load_json_config(conn))
    return {**cfg, **auth_status(conn, cfg)}


def save_settings(
    conn: sqlite3.Connection,
    *,
    root_path: str | None = None,
    sync_interval_minutes=None,
    auth_source: str | None = None,
    client_id: str | None = None,
    tenant: str | None = None,
    token_env_var: str | None = None,
) -> dict:
    current = _base_settings(_load_json_config(conn))
    if root_path is not None:
        current["root_path"] = normalize_root_path(root_path)
    if sync_interval_minutes is not None:
        current["sync_interval_minutes"] = normalize_interval_minutes(sync_interval_minutes)
    if auth_source is not None:
        current["auth_source"] = _auth_source(auth_source)
    if client_id is not None:
        current["client_id"] = _client_id(client_id)
    if tenant is not None:
        current["tenant"] = _tenant(tenant)
    if token_env_var is not None:
        current["token_env_var"] = _env_var(token_env_var)
    payload = {
        "root_path": current["root_path"],
        "sync_interval_minutes": current["sync_interval_minutes"],
        "auth_source": current["auth_source"],
        "client_id": current["client_id"],
        "tenant": current["tenant"],
        "token_env_var": current["token_env_var"],
    }
    save_server_config(conn, {CONFIG_KEY: json.dumps(payload)}, bump_revision=False)
    return get_settings(conn)


def user_remote_root(root_path: str, username: str) -> str:
    parts = split_drive_path(root_path) or [DEFAULT_ROOT]
    parts.append(_safe_user_folder(username))
    return "/".join(parts)


def snapshots_remote_path(root_path: str, username: str) -> str:
    return f"{user_remote_root(root_path, username)}/snapshots"
