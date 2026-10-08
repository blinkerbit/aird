"""Path access for GitLab bindings and file comments."""

from __future__ import annotations

from dataclasses import dataclass

from aird.core.share_root import (
    creator_folder_username_from_share_field,
    filesystem_root_for_share,
    login_matches_share_creator_field,
)
from aird.db.shares import get_share_by_id, share_covers_relative_path
from aird.handlers.share_handlers import (
    _check_share_access,
    _is_user_allowed_for_modify,
)


@dataclass
class PathAccess:
    owner_username: str
    rel_path: str
    can_read: bool
    can_write: bool
    is_self: bool
    share_id: str | None = None


def _path_under_share_root(rel: str, root_rel: str) -> bool:
    return bool(root_rel) and (rel == root_rel or rel.startswith(root_rel + "/"))


def _rel_in_share(share: dict, rel: str, conn, root: str) -> bool:
    if not rel:
        return True
    if share_covers_relative_path(conn, share, rel, root):
        return True
    for raw in share.get("paths") or []:
        root_rel = str(raw).replace("\\", "/").strip("/")
        if _path_under_share_root(rel, root_rel):
            return True
    return False


def _norm(path: str) -> str:
    text = (path or "").replace("\\", "/").strip("/")
    if ".." in text.split("/"):
        raise ValueError("Invalid path")
    return text


def _share_path_access(handler, *, rel: str, share_id: str, username: str) -> PathAccess | None:
    conn = handler.db_conn
    share = get_share_by_id(conn, share_id) if conn else None
    if not share:
        return None
    allowed, _, _ = _check_share_access(
        share,
        share_id,
        handler.request,
        handler.get_cookie,
        handler.get_secure_cookie,
    )
    if not allowed:
        return None
    root = filesystem_root_for_share(share)
    if not _rel_in_share(share, rel, conn, root):
        return None
    owner = creator_folder_username_from_share_field(share.get("created_by"))
    if not owner:
        return None
    can_write = bool(
        (
            username
            and login_matches_share_creator_field(share.get("created_by"), username)
        )
        or _is_user_allowed_for_modify(share, handler.get_secure_cookie)[0]
    )
    return PathAccess(
        owner_username=owner,
        rel_path=rel,
        can_read=True,
        can_write=can_write,
        is_self=False,
        share_id=share_id,
    )


def resolve_access(handler, *, path: str, share_id: str | None) -> PathAccess | None:
    """Owner browse is is_self; share viewers never get is_self (no owner GitLab token)."""
    from aird.handlers.base_handler import get_username_string_for_db

    username = get_username_string_for_db(handler) or ""
    rel = _norm(path)
    if share_id:
        return _share_path_access(handler, rel=rel, share_id=share_id, username=username)
    if not username:
        return None
    return PathAccess(
        owner_username=username,
        rel_path=rel,
        can_read=True,
        can_write=True,
        is_self=True,
        share_id=None,
    )
