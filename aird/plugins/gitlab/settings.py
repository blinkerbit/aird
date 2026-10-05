"""Admin-controlled GitLab plugin defaults (boards, host, token)."""

from __future__ import annotations

import json
import os
import re
import sqlite3

from aird.constants.input_limits import (
    GITLAB_BOARD_ID_MAX,
    GITLAB_HOST_MAX_LEN,
    GITLAB_PROJECT_PATH_MAX_LEN,
    GITLAB_TOKEN_MAX_LEN,
)
from aird.core.secret_storage import decrypt_secret, encrypt_secret
from aird.db.config import load_server_config, save_server_config

CONFIG_KEY = "gitlab_plugin"
TOKEN_CONFIG_KEY = "gitlab_plugin_token"
DEFAULT_HOST = "https://gitlab.com"
DEFAULT_ENV_VAR = "GITLAB_TOKEN"
TOKEN_SOURCE_ENV = "env"
TOKEN_SOURCE_STORED = "stored"
TOKEN_SOURCES = frozenset({TOKEN_SOURCE_ENV, TOKEN_SOURCE_STORED})
MAX_BOARDS = 20
MAX_BOARD_LABEL = 80
_ENV_VAR_RE = re.compile(r"^[A-Za-z_]\w{0,63}$")
_FALLBACK_ENV_VARS = ("GITLAB_TOKEN", "GL_TOKEN", "GITLAB_PRIVATE_TOKEN")


def _host(raw: str | None) -> str:
    value = (raw or DEFAULT_HOST).strip() or DEFAULT_HOST
    if len(value) > GITLAB_HOST_MAX_LEN:
        return DEFAULT_HOST
    if not value.startswith("http"):
        value = "https://" + value
    return value.rstrip("/")


def _project(raw: str | None) -> str:
    value = (raw or "").strip().strip("/")
    if len(value) > GITLAB_PROJECT_PATH_MAX_LEN:
        return ""
    return value


def _board_iid(raw) -> int | None:
    if raw in (None, ""):
        return None
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return None
    if value < 1 or value > GITLAB_BOARD_ID_MAX:
        return None
    return value


def _token_source(raw: str | None) -> str:
    value = (raw or TOKEN_SOURCE_ENV).strip().lower()
    return value if value in TOKEN_SOURCES else TOKEN_SOURCE_ENV


def _env_var(raw: str | None) -> str:
    value = (raw or DEFAULT_ENV_VAR).strip()
    if value and _ENV_VAR_RE.match(value):
        return value
    return DEFAULT_ENV_VAR


def _normalize_board(raw: dict) -> dict | None:
    if not isinstance(raw, dict):
        return None
    label = str(raw.get("label") or "").strip()[:MAX_BOARD_LABEL]
    issues = _project(str(raw.get("issues_project") or ""))
    if not label or not issues:
        return None
    code = _project(str(raw.get("code_project") or "")) or issues
    return {
        "label": label,
        "gitlab_host": _host(str(raw.get("gitlab_host") or DEFAULT_HOST)),
        "issues_project": issues,
        "code_project": code,
        "board_iid": _board_iid(raw.get("board_iid")),
    }


def normalize_boards(raw) -> list[dict]:
    if not isinstance(raw, list):
        return []
    out: list[dict] = []
    seen: set[str] = set()
    for item in raw[:MAX_BOARDS]:
        board = _normalize_board(item)
        if not board:
            continue
        key = f"{board['gitlab_host']}|{board['issues_project']}|{board.get('board_iid') or 0}"
        if key in seen:
            continue
        seen.add(key)
        out.append(board)
    return out


def _stored_token_ciphertext(conn: sqlite3.Connection | None) -> str:
    if conn is None:
        return ""
    return (load_server_config(conn).get(TOKEN_CONFIG_KEY) or "").strip()


def load_stored_token(conn: sqlite3.Connection | None) -> str | None:
    raw = _stored_token_ciphertext(conn)
    if not raw:
        return None
    value = decrypt_secret(raw).strip()
    return value or None


def save_stored_token(conn: sqlite3.Connection, token: str) -> None:
    value = (token or "").strip()
    if not value or len(value) > GITLAB_TOKEN_MAX_LEN:
        raise ValueError("GitLab token is required")
    save_server_config(
        conn, {TOKEN_CONFIG_KEY: encrypt_secret(value)}, bump_revision=False
    )


def clear_stored_token(conn: sqlite3.Connection) -> None:
    save_server_config(conn, {TOKEN_CONFIG_KEY: ""}, bump_revision=False)


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


def resolve_server_token(conn: sqlite3.Connection | None) -> str | None:
    settings = get_settings(conn)
    if settings["token_source"] == TOKEN_SOURCE_STORED:
        return load_stored_token(conn)
    return _env_token(settings["token_env_var"])


def _base_settings(data: dict | None = None) -> dict:
    parsed = data if isinstance(data, dict) else {}
    return {
        "default_host": _host(str(parsed.get("default_host") or DEFAULT_HOST)),
        "boards": normalize_boards(parsed.get("boards")),
        "token_source": _token_source(str(parsed.get("token_source") or TOKEN_SOURCE_ENV)),
        "token_env_var": _env_var(str(parsed.get("token_env_var") or DEFAULT_ENV_VAR)),
    }


def token_status(conn: sqlite3.Connection | None, settings: dict | None = None) -> dict:
    cfg = settings or _base_settings({})
    if cfg["token_source"] == TOKEN_SOURCE_STORED:
        configured = bool(load_stored_token(conn))
        detail = "Stored in Aird" if configured else "No token saved"
    else:
        var = cfg["token_env_var"]
        value = _env_token(var)
        configured = bool(value)
        detail = f"{var} is set" if configured else f"{var} not set in environment"
    return {
        "token_source": cfg["token_source"],
        "token_env_var": cfg["token_env_var"],
        "token_configured": configured,
        "token_status_detail": detail,
        "stored_token_configured": bool(load_stored_token(conn)),
    }


def get_settings(conn: sqlite3.Connection | None) -> dict:
    defaults = _base_settings({})
    if conn is None:
        return {**defaults, **token_status(None, defaults)}
    raw = load_server_config(conn).get(CONFIG_KEY)
    data: dict = {}
    if raw:
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, dict):
                data = parsed
        except (TypeError, ValueError):
            data = {}
    settings = _base_settings(data)
    return {**settings, **token_status(conn, settings)}


def save_settings(
    conn: sqlite3.Connection,
    *,
    default_host: str | None = None,
    boards: list | None = None,
    token_source: str | None = None,
    token_env_var: str | None = None,
) -> dict:
    raw = load_server_config(conn).get(CONFIG_KEY)
    data: dict = {}
    if raw:
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, dict):
                data = parsed
        except (TypeError, ValueError):
            data = {}
    current = _base_settings(data)
    if default_host is not None:
        current["default_host"] = _host(default_host)
    if boards is not None:
        current["boards"] = normalize_boards(boards)
    if token_source is not None:
        current["token_source"] = _token_source(token_source)
    if token_env_var is not None:
        current["token_env_var"] = _env_var(token_env_var)
    payload = {
        "default_host": current["default_host"],
        "boards": current["boards"],
        "token_source": current["token_source"],
        "token_env_var": current["token_env_var"],
    }
    save_server_config(conn, {CONFIG_KEY: json.dumps(payload)}, bump_revision=False)
    return get_settings(conn)
