"""Per-user OneDrive Graph token. Encrypted in SQLite for assigned users."""

from __future__ import annotations

import logging
import sqlite3
import stat
from pathlib import Path

from aird.core.user_storage import user_home_for_username
from aird.db.plugin_secrets import (
    SECRET_ONEDRIVE,
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
    return _secrets_dir(username) / "onedrive_token"


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
        set_plugin_secret(conn, username, SECRET_ONEDRIVE, value)
        path.unlink()
    except Exception:
        logger.debug("OneDrive token file migrate skipped", exc_info=True)
        return value
    return value


def save_user_token(
    username: str, token: str, conn: sqlite3.Connection | None = None
) -> None:
    set_plugin_secret(conn, username, SECRET_ONEDRIVE, token)
    try:
        token_file_for(username).unlink()
    except OSError:
        pass


def delete_user_token(
    username: str, conn: sqlite3.Connection | None = None
) -> None:
    delete_plugin_secret(conn, username, SECRET_ONEDRIVE)
    try:
        token_file_for(username).unlink()
    except OSError:
        pass


def load_user_token(
    username: str, conn: sqlite3.Connection | None = None
) -> str | None:
    from aird.plugins.onedrive.settings import resolve_access_token

    server = resolve_access_token(conn)
    if server:
        return server
    value = get_plugin_secret(conn, username, SECRET_ONEDRIVE)
    if value:
        return value
    return _migrate_file_token(username, conn)


def token_configured(
    username: str, conn: sqlite3.Connection | None = None
) -> bool:
    from aird.plugins.onedrive.settings import resolve_access_token

    if resolve_access_token(conn):
        return True
    return bool(load_user_token(username, conn=conn))
