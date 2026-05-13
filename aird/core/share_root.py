"""Multi-user storage root and creator identity derived from share rows."""

from __future__ import annotations

import os
import re

import aird.constants as constants_module
from aird.core.security import sanitize_username_for_folder

# Must match base_handler token display labels (avoid importing handlers here).
_DISPLAY_ADMIN_TOKEN = "Admin (Token)"  # nosec B105
_DISPLAY_ACCESS_TOKEN = "Access (Token)"  # nosec B105
_TOKEN_ONLY_USERNAMES = frozenset({"token_user", "admin_token"})
_CREATOR_ROLE_DISPLAY = re.compile(r"^(.+?) \((Admin|User)\)$")


def login_matches_share_creator_field(
    creator_field: str | None, username: str | None
) -> bool:
    if not username:
        return False
    c = (creator_field or "").strip()
    if not c:
        return False
    if c == username:
        return True
    m = _CREATOR_ROLE_DISPLAY.match(c)
    return bool(m and m.group(1).strip() == username)


def creator_folder_username_from_share_field(created_by: str | None) -> str:
    """Login / folder key from stored created_by (handles legacy display suffix)."""
    c = (created_by or "").strip()
    if not c or c in (_DISPLAY_ADMIN_TOKEN, _DISPLAY_ACCESS_TOKEN):
        return ""
    m = _CREATOR_ROLE_DISPLAY.match(c)
    if m:
        return m.group(1).strip()
    return c


def filesystem_root_for_share(share: dict) -> str:
    """Directory relative to which *share*'s local paths and tag scans are resolved."""
    if not constants_module.MULTI_USER:
        return constants_module.ROOT_DIR
    login = creator_folder_username_from_share_field(share.get("created_by"))
    if not login or login in _TOKEN_ONLY_USERNAMES:
        return constants_module.ROOT_DIR
    safe = sanitize_username_for_folder(login)
    if not safe:
        return constants_module.ROOT_DIR
    user_root = os.path.join(constants_module.ROOT_DIR, safe)
    os.makedirs(user_root, exist_ok=True)
    return user_root
