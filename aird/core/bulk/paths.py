"""Path helpers for bulk transfers."""

from __future__ import annotations

import os

import aird.constants as constants_module
from aird.core.security import is_within_root
from aird.handlers.file_op_handlers import _validate_upload_destination

_TOKEN_ONLY_USERNAMES = {"token_user", "admin_token"}


def user_root_for_username(username: str) -> str:
    from aird.core.user_storage import user_data_dir_for_username

    if not constants_module.MULTI_USER:
        return constants_module.ROOT_DIR
    if not username or username in _TOKEN_ONLY_USERNAMES:
        return constants_module.ROOT_DIR
    return user_data_dir_for_username(
        username,
        root_dir=constants_module.ROOT_DIR,
        multi_user=True,
    )


def resolve_download_path(relpath: str, user_root: str) -> tuple[str | None, str | None]:
    from aird.core.user_storage import confine_root_for_rel, join_user_rel

    rel = (relpath or "").strip().strip("/")
    if not rel:
        return None, "invalid path"
    abspath = os.path.realpath(join_user_rel(user_root, rel))
    confine = os.path.realpath(confine_root_for_rel(user_root, rel))
    if not is_within_root(abspath, confine):
        return None, "access denied"
    if not os.path.isfile(abspath):
        return None, "not found"
    return abspath, None


def validate_upload_path(
    upload_dir: str, filename: str, user_root: str
) -> tuple[str | None, tuple[int, str] | None]:
    return _validate_upload_destination(upload_dir, filename, user_root)
