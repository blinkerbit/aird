"""Rename an AIRD account and the records that store its username."""

from __future__ import annotations

import json
import logging
import os
import re
import sqlite3

from aird.core.security import legacy_folder_name, sanitize_username_for_folder
from aird.db.users import get_user_by_username

logger = logging.getLogger(__name__)

_USERNAME_RE = re.compile(r"[A-Za-z0-9_-]{3,50}")
_IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_SKIP_TABLES = frozenset({"network_shares", "audit_log", "sqlite_sequence"})
_IDENTITY_COLUMNS = frozenset(
    {
        "username",
        "owner_username",
        "author_username",
        "sender_username",
        "created_by",
        "updated_by",
        "pinned_by",
        "owner_username",
    }
)
_JSON_LISTS = (
    ("shares", "allowed_users"),
    ("shares", "modify_users"),
    ("plugin_access", "usernames_json"),
)
_LOCKED = frozenset({"admin_token", "token_user", "admin_token_authenticated"})
_MAILBOX = "mailbox.sqlite3"


class RenameError(ValueError):
    pass


def username_from_display_name(display_name: str) -> str:
    """Turn an Authentik display name into an AIRD username.

    Spaces become underscores. Other characters that AIRD usernames cannot
    contain are dropped. An empty result means the name cannot be used.
    """
    text = re.sub(r"\s+", "_", (display_name or "").strip())
    text = re.sub(r"[^A-Za-z0-9_-]", "", text)
    text = re.sub(r"_+", "_", text).strip("_")
    if not _USERNAME_RE.fullmatch(text):
        return ""
    if text in _LOCKED:
        return ""
    return text


def sync_authentik_display_name(
    conn: sqlite3.Connection,
    user_id: int,
    stable_username: str,
    display_name: str = "",
    *,
    root_dir: str | None = None,
    multi_user: bool | None = None,
) -> str:
    """Link the stable Authentik username. AIRD username and display_name stay admin-managed.

    ``display_name`` / ``root_dir`` / ``multi_user`` are kept for call-site compatibility
    and are ignored: renaming from Authentik broke chat keys, so only the mapping is stored.
    """
    _ = (display_name, root_dir, multi_user)
    from aird.db.users import set_authentik_username

    row = conn.execute("SELECT username FROM users WHERE id = ?", (user_id,)).fetchone()
    if row is None:
        return ""
    current = str(row[0])
    stable = (stable_username or "").strip()
    if stable:
        set_authentik_username(conn, user_id, stable)
    return current


def rename_user(
    conn: sqlite3.Connection,
    user_id: int,
    new_username: str,
    *,
    root_dir: str | None = None,
    multi_user: bool | None = None,
) -> str:
    """Rename the account. Return the previous username."""
    new_username = (new_username or "").strip()
    if not _USERNAME_RE.fullmatch(new_username) or new_username in _LOCKED:
        raise RenameError("That name cannot be used as a username")
    row = conn.execute(
        "SELECT id, username FROM users WHERE id = ?",
        (user_id,),
    ).fetchone()
    if row is None:
        raise RenameError("User not found")
    old_username = str(row[1])
    if old_username in _LOCKED:
        raise RenameError("That account cannot be renamed")
    if old_username == new_username:
        return old_username
    taken = get_user_by_username(conn, new_username)
    if taken is not None and int(taken["id"]) != int(user_id):
        raise RenameError("That username is already in use")

    root, multi = _storage(root_dir, multi_user)
    old_home = _existing_account_dir(root, old_username) if multi else None
    new_home = os.path.join(root, sanitize_username_for_folder(new_username) or "") if multi else None
    if old_home and new_home and os.path.normcase(old_home) != os.path.normcase(new_home):
        if os.path.exists(new_home):
            raise RenameError("A folder for that name already exists")

    with conn:
        conn.execute("UPDATE users SET username = ? WHERE id = ?", (new_username, user_id))
        _rename_identity_columns(conn, old_username, new_username)
        _rename_json_lists(conn, old_username, new_username)

    _rename_storage(root, old_username, new_username, multi, old_home, new_home)
    return old_username


def _storage(root_dir: str | None, multi_user: bool | None) -> tuple[str, bool]:
    import aird.constants as constants_module

    root = constants_module.ROOT_DIR if root_dir is None else root_dir
    multi = constants_module.MULTI_USER if multi_user is None else multi_user
    return root, bool(multi)


def _quote(name: str) -> str:
    if not _IDENT.fullmatch(name):
        raise RenameError("Unexpected database name")
    return '"' + name + '"'


def _table_names(conn: sqlite3.Connection) -> list[str]:
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
    ).fetchall()
    return [str(row[0]) for row in rows]


def _columns(conn: sqlite3.Connection, table: str) -> list[str]:
    rows = conn.execute(f"PRAGMA table_info({_quote(table)})").fetchall()
    return [str(row[1]) for row in rows]


def _rename_identity_columns(conn: sqlite3.Connection, old: str, new: str) -> None:
    for table in _table_names(conn):
        if table in _SKIP_TABLES or table == "users":
            continue
        for column in _columns(conn, table):
            if column not in _IDENTITY_COLUMNS:
                continue
            conn.execute(
                f"UPDATE {_quote(table)} SET {_quote(column)} = ? WHERE {_quote(column)} = ?",
                (new, old),
            )


def _rename_json_lists(conn: sqlite3.Connection, old: str, new: str) -> None:
    present = set(_table_names(conn))
    for table, column in _JSON_LISTS:
        if table not in present or column not in _columns(conn, table):
            continue
        rows = conn.execute(
            f"SELECT rowid, {_quote(column)} FROM {_quote(table)} WHERE {_quote(column)} IS NOT NULL"
        ).fetchall()
        for rowid, raw in rows:
            updated = _replace_json_name(raw, old, new)
            if updated != raw:
                conn.execute(
                    f"UPDATE {_quote(table)} SET {_quote(column)} = ? WHERE rowid = ?",
                    (updated, rowid),
                )


def _replace_json_name(raw: str, old: str, new: str) -> str:
    try:
        data = json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return raw
    if not isinstance(data, list):
        return raw
    changed = [new if item == old else item for item in data]
    if changed == data:
        return raw
    return json.dumps(changed)


def _existing_account_dir(root: str, username: str) -> str | None:
    names = []
    safe = sanitize_username_for_folder(username)
    legacy = legacy_folder_name(username)
    for name in (safe, legacy):
        if name and name not in names:
            names.append(name)
    for name in names:
        path = os.path.join(root, name)
        if os.path.isdir(path):
            return path
    return None


def _rename_storage(
    root: str,
    old: str,
    new: str,
    multi: bool,
    old_home: str | None,
    new_home: str | None,
) -> None:
    try:
        if multi and old_home and new_home and os.path.normcase(old_home) != os.path.normcase(new_home):
            os.rename(old_home, new_home)
        elif not multi:
            _rename_single_user_mailbox_dir(root, old, new)
        if os.path.isdir(root):
            _rewrite_mailboxes(root, old, new)
        _migrate_e2e_identity(root, old, new, multi)
    except OSError:
        logger.exception("Renamed %s to %s in the database, but file storage could not be moved", old, new)


def _migrate_e2e_identity(root: str, old: str, new: str, multi: bool) -> None:
    """Keep chat E2E private keys across a rename; only the username label changes."""
    from aird.constants import CHAT_STORE_FOLDER
    from aird.plugins.chat.e2e import IDENTITY_FILENAME

    old_safe = sanitize_username_for_folder(old)
    new_safe = sanitize_username_for_folder(new)
    if not old_safe or not new_safe:
        return

    candidates: list[str] = []
    if multi:
        candidates.append(os.path.join(root, new_safe, CHAT_STORE_FOLDER, IDENTITY_FILENAME))
        candidates.append(os.path.join(root, old_safe, CHAT_STORE_FOLDER, IDENTITY_FILENAME))
    candidates.append(os.path.join(root, CHAT_STORE_FOLDER, new_safe, IDENTITY_FILENAME))
    candidates.append(os.path.join(root, CHAT_STORE_FOLDER, old_safe, IDENTITY_FILENAME))

    src = next((path for path in candidates if os.path.isfile(path)), None)
    if src is None:
        return

    if multi:
        dest_dir = os.path.join(root, new_safe, CHAT_STORE_FOLDER)
    else:
        dest_dir = os.path.join(root, CHAT_STORE_FOLDER, new_safe)
    dest = os.path.join(dest_dir, IDENTITY_FILENAME)

    try:
        import json

        with open(src, encoding="utf-8") as fh:
            data = json.load(fh)
        if not isinstance(data, dict):
            return
        data["username"] = new
        os.makedirs(dest_dir, exist_ok=True)
        tmp = f"{dest}.{os.getpid()}.tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(data, fh, separators=(",", ":"))
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, dest)
        if os.path.normcase(os.path.abspath(src)) != os.path.normcase(os.path.abspath(dest)):
            try:
                os.remove(src)
            except OSError:
                pass
    except (OSError, TypeError, ValueError):
        logger.exception("Could not migrate chat E2E identity from %s to %s", old, new)


def _rename_single_user_mailbox_dir(root: str, old: str, new: str) -> None:
    from aird.constants import CHAT_STORE_FOLDER

    old_name = sanitize_username_for_folder(old)
    new_name = sanitize_username_for_folder(new)
    if not old_name or not new_name or old_name == new_name:
        return
    src = os.path.join(root, CHAT_STORE_FOLDER, old_name)
    dst = os.path.join(root, CHAT_STORE_FOLDER, new_name)
    if os.path.isdir(src) and not os.path.exists(dst):
        os.rename(src, dst)


def _rewrite_mailboxes(root: str, old: str, new: str) -> None:
    for dirpath, _dirnames, filenames in os.walk(root):
        if _MAILBOX not in filenames:
            continue
        _rewrite_mailbox(os.path.join(dirpath, _MAILBOX), old, new)


def _rewrite_mailbox(path: str, old: str, new: str) -> None:
    conn = sqlite3.connect(path)
    try:
        tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        statements = (
            ("members", "username"),
            ("messages", "sender_username"),
            ("receipts", "username"),
            ("reactions", "username"),
            ("e2e_wraps", "username"),
            ("pins", "pinned_by"),
        )
        for table, column in statements:
            if table not in tables:
                continue
            conn.execute(
                f"UPDATE {_quote(table)} SET {_quote(column)} = ? WHERE {_quote(column)} = ?",
                (new, old),
            )
        conn.commit()
    except sqlite3.Error:
        logger.exception("Could not rename %s inside %s", old, path)
    finally:
        conn.close()
