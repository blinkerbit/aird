"""Path helpers for bulk transfers."""

from __future__ import annotations

import os

import aird.constants as constants_module
from aird.core.security import is_within_root, legacy_folder_name, sanitize_username_for_folder
from aird.handlers.file_op_handlers import _validate_upload_destination

_TOKEN_ONLY_USERNAMES = {"token_user", "admin_token"}


def user_root_for_username(username: str) -> str:
    if not constants_module.MULTI_USER:
        return constants_module.ROOT_DIR
    if not username or username in _TOKEN_ONLY_USERNAMES:
        return constants_module.ROOT_DIR
    safe_name = sanitize_username_for_folder(username)
    if not safe_name:
        return constants_module.ROOT_DIR
    user_root = os.path.join(constants_module.ROOT_DIR, safe_name)
    if not os.path.isdir(user_root):
        legacy = legacy_folder_name(username)
        if legacy and legacy != safe_name:
            legacy_root = os.path.join(constants_module.ROOT_DIR, legacy)
            if os.path.isdir(legacy_root):
                user_root = legacy_root
    os.makedirs(user_root, exist_ok=True)
    return user_root


def resolve_download_path(relpath: str, user_root: str) -> tuple[str | None, str | None]:
    rel = (relpath or "").strip().strip("/")
    if not rel:
        return None, "invalid path"
    abspath = os.path.realpath(os.path.join(user_root, rel))
    if not is_within_root(abspath, user_root):
        return None, "access denied"
    if not os.path.isfile(abspath):
        return None, "not found"
    return abspath, None


def validate_upload_path(
    upload_dir: str, filename: str, user_root: str
) -> tuple[str | None, tuple[int, str] | None]:
    return _validate_upload_destination(upload_dir, filename, user_root)
