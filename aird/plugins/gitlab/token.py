"""Resolve the folder owner's GitLab PAT (self only). Never used for share viewers.

Preferred store: encrypted row in SQLite (same Fernet key as share passwords).
Legacy plaintext files are migrated into the DB on first read, then removed.
"""

from __future__ import annotations

import logging
import os
import sqlite3
import stat
from pathlib import Path

from aird.core.user_storage import user_home_for_username
from aird.db.plugin_secrets import (
    SECRET_GITLAB,
    delete_plugin_secret,
    get_plugin_secret,
    set_plugin_secret,
)

logger = logging.getLogger(__name__)


def _secrets_dir(username: str) -> Path:
    home = user_home_for_username(username)
    path = Path(home) / ".aird" / "secrets"
    path.mkdir(parents=True, exist_ok=True)
    try:
        path.chmod(stat.S_IRWXU)
    except OSError:
        pass
    return path


def token_file_for(username: str) -> Path:
    return _secrets_dir(username) / "gitlab_token"


def _migrate_file_token(username: str, conn: sqlite3.Connection | None) -> str | None:
    path = token_file_for(username)
    if not path.is_file():
        return None
    try:
        value = path.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    if not value:
        try:
            path.unlink()
        except OSError:
            pass
        return None
    try:
        set_plugin_secret(conn, username, SECRET_GITLAB, value)
        path.unlink()
    except Exception:
        logger.debug("GitLab token file migrate skipped", exc_info=True)
        return value
    return value


def save_user_token(
    username: str, token: str, conn: sqlite3.Connection | None = None
) -> None:
    set_plugin_secret(conn, username, SECRET_GITLAB, token)
    try:
        token_file_for(username).unlink()
    except OSError:
        pass


def delete_user_token(
    username: str, conn: sqlite3.Connection | None = None
) -> None:
    delete_plugin_secret(conn, username, SECRET_GITLAB)
    try:
        token_file_for(username).unlink()
    except OSError:
        pass


def _glab_config_paths() -> list[Path]:
    candidates: list[Path] = []
    appdata = os.environ.get("APPDATA") or os.environ.get("XDG_CONFIG_HOME")
    if appdata:
        candidates.append(Path(appdata) / "glab-cli" / "config.yml")
    candidates.append(Path.home() / ".config" / "glab-cli" / "config.yml")
    return candidates


def _normalize_glab_host_key(host: str) -> str:
    return (host or "gitlab.com").replace("https://", "").replace("http://", "").split("/")[0]


def _token_from_glab_yaml(text: str, host_key: str) -> str | None:
    in_hosts = False
    in_host = False
    for raw in text.splitlines():
        stripped = raw.rstrip().strip()
        if stripped.startswith("hosts:"):
            in_hosts = True
            in_host = False
            continue
        if not in_hosts:
            continue
        if stripped.endswith(":") and not stripped.startswith("token"):
            name = stripped[:-1].strip().strip("'\"")
            in_host = name == host_key or name.endswith(host_key)
            continue
        if in_host and stripped.startswith("token:"):
            value = stripped.split(":", 1)[1].strip().strip("'\"")
            if value:
                return value
    return None


def _read_glab_token(host: str) -> str | None:
    host_key = _normalize_glab_host_key(host)
    for path in _glab_config_paths():
        if not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        token = _token_from_glab_yaml(text, host_key)
        if token:
            return token
    return None


def load_owner_token(
    username: str,
    host: str = "https://gitlab.com",
    conn: sqlite3.Connection | None = None,
) -> str | None:
    from aird.plugins.gitlab.settings import resolve_server_token

    server = resolve_server_token(conn)
    if server:
        return server
    value = get_plugin_secret(conn, username, SECRET_GITLAB)
    if value:
        return value
    migrated = _migrate_file_token(username, conn)
    if migrated:
        return migrated
    for env_name in ("GITLAB_TOKEN", "GL_TOKEN", "GITLAB_PRIVATE_TOKEN"):
        env_value = (os.environ.get(env_name) or "").strip()
        if env_value:
            return env_value
    return _read_glab_token(host)


def token_configured(
    username: str,
    host: str = "https://gitlab.com",
    conn: sqlite3.Connection | None = None,
) -> bool:
    from aird.plugins.gitlab.settings import resolve_server_token

    if resolve_server_token(conn):
        return True
    return bool(load_owner_token(username, host, conn=conn))
