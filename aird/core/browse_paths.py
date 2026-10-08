"""Resolve browse-relative paths across personal data and admin path mounts."""

from __future__ import annotations

import logging
import os
from datetime import datetime

from aird.core.security import is_within_root
from aird.core.user_storage import (
    USER_CHATS_DIRNAME,
    USER_SHARES_DIRNAME,
    confine_root_for_rel,
    join_user_rel,
)
from aird.db.user_mounts import list_user_path_mounts

logger = logging.getLogger(__name__)
_RUNTIME_DB = None

_RESERVED_MOUNT_NAMES = frozenset(
    {USER_SHARES_DIRNAME, USER_CHATS_DIRNAME, ".aird", ".", ".."}
)


def normalize_rel(rel: str) -> str:
    return (rel or "").replace("\\", "/").strip().strip("/")


def sanitize_mount_name(name: str) -> str | None:
    cleaned = (name or "").strip()
    if not cleaned or cleaned in _RESERVED_MOUNT_NAMES:
        return None
    if "/" in cleaned or "\\" in cleaned or cleaned.startswith("."):
        return None
    if len(cleaned) > 64:
        return None
    return cleaned


def split_first(rel: str) -> tuple[str, str]:
    norm = normalize_rel(rel)
    if not norm:
        return "", ""
    if "/" not in norm:
        return norm, ""
    first, rest = norm.split("/", 1)
    return first, rest


def mounts_for_username(conn, username: str) -> list[dict]:
    if conn is None or not username:
        return []
    return list_user_path_mounts(conn, username)


def resolve_rel(
    data_root: str, rel: str, mounts: list[dict] | None = None
) -> tuple[str | None, str | None]:
    """Map a user-relative path to ``(abspath, confine_root)``.

    Empty *rel* resolves to the personal data root. A first path segment that
    matches an admin mount maps onto that host directory; anything else stays
    under *data_root* (including ``.aird-shares`` / ``.aird-chats``).
    """
    mounts = mounts or []
    norm = normalize_rel(rel)
    if not norm:
        root = os.path.abspath(data_root)
        return root, root

    first, rest = split_first(norm)
    by_name = {m["mount_name"]: m for m in mounts if m.get("mount_name")}
    mount = by_name.get(first)
    if mount:
        host = os.path.abspath(str(mount["host_path"]))
        abspath = (
            os.path.abspath(os.path.join(host, rest.replace("/", os.sep)))
            if rest
            else host
        )
        if not is_within_root(abspath, host):
            return None, None
        return abspath, host

    abspath = join_user_rel(data_root, norm)
    confine = os.path.abspath(confine_root_for_rel(data_root, norm))
    if not is_within_root(abspath, confine):
        return None, None
    return abspath, confine


def resolve_for_user(
    username: str, rel: str, data_root: str, conn
) -> tuple[str | None, str | None]:
    return resolve_rel(data_root, rel, mounts_for_username(conn, username))


def mounts_for_share_creator(conn, share: dict) -> list[dict]:
    from aird.core.share_root import creator_folder_username_from_share_field

    login = creator_folder_username_from_share_field(share.get("created_by"))
    return mounts_for_username(conn, login)


def resolve_share_rel(
    share: dict, rel: str, conn
) -> tuple[str | None, str | None]:
    from aird.core.share_root import filesystem_root_for_share

    return resolve_rel(
        filesystem_root_for_share(share),
        rel,
        mounts_for_share_creator(conn, share),
    )


def mount_for_rel(rel: str, mounts: list[dict] | None) -> dict | None:
    first, _rest = split_first(normalize_rel(rel))
    if not first:
        return None
    by_name = {m["mount_name"]: m for m in (mounts or []) if m.get("mount_name")}
    return by_name.get(first)


def is_mount_root(rel: str, mounts: list[dict] | None) -> bool:
    first, rest = split_first(normalize_rel(rel))
    if not first or rest:
        return False
    return mount_for_rel(first, mounts) is not None


def is_writable_rel(rel: str, mounts: list[dict] | None) -> bool:
    mount = mount_for_rel(rel, mounts)
    if not mount:
        return True
    return bool(mount.get("writable", True))


def write_blocked_reason(
    rel: str, mounts: list[dict] | None, *, as_target: bool = False
) -> str | None:
    """Why a virtual path cannot be mutated, or None if writes are allowed.

    *as_target* is True when operating on the path itself (delete/rename).
    """
    if as_target and is_mount_root(rel, mounts):
        return "assigned"
    if not is_writable_rel(rel, mounts):
        return "readonly"
    return None


def lazy_runtime_db():
    """Read-only-ish SQLite handle for processes without app context (transfer)."""
    global _RUNTIME_DB
    if _RUNTIME_DB is not None:
        return _RUNTIME_DB
    try:
        from aird.database.db import get_data_dir

        path = os.path.join(get_data_dir(), "aird.sqlite3")
        if not os.path.isfile(path):
            return None
        import sqlite3

        conn = sqlite3.connect(path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        _RUNTIME_DB = conn
        return conn
    except Exception:
        logger.debug("lazy_runtime_db failed", exc_info=True)
        return None


def overlay_mount_entries(
    files: list[dict], mounts: list[dict], current_path: str
) -> list[dict]:
    """Add admin-assigned folders to the virtual root listing."""
    if normalize_rel(current_path):
        return files
    by_name = {f.get("name"): i for i, f in enumerate(files)}
    for mount in mounts:
        name = mount.get("mount_name")
        if not name:
            continue
        host = mount.get("host_path") or ""
        mtime = 0
        try:
            mtime = int(os.path.getmtime(host))
        except OSError:
            logger.debug("mount host missing: %s", host)
        entry = {
            "name": name,
            "is_dir": True,
            "is_mount": True,
            "writable": bool(mount.get("writable", True)),
            "child_count": None,
            "size_bytes": 0,
            "size_str": "Assigned",
            "modified": datetime.fromtimestamp(mtime).strftime("%Y-%m-%d %H:%M:%S")
            if mtime
            else "",
            "modified_timestamp": mtime,
        }
        idx = by_name.get(name)
        if idx is not None:
            files[idx] = entry
        else:
            by_name[name] = len(files)
            files.append(entry)
    return files
