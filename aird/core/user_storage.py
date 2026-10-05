"""Per-user storage layout.

Multi-user home::

    ROOT/{username}/
      .aird-shares/   received / shared files
      .aird-chats/    chat transcripts and chat file copies
      data/           browsable user files (get_user_root)

Single-user mode keeps ROOT as the browse root; ``.aird-shares`` and
``.aird-chats`` live beside files in ROOT.
"""

from __future__ import annotations

import logging
import os
import shutil

import aird.constants as constants_module
from aird.constants import AIRD_META_FOLDER
from aird.core.security import legacy_folder_name, sanitize_username_for_folder

logger = logging.getLogger(__name__)

USER_DATA_DIRNAME = "data"
USER_SHARES_DIRNAME = ".aird-shares"
USER_CHATS_DIRNAME = ".aird-chats"

_TOKEN_ONLY_USERNAMES = {"token_user", "admin_token"}
_RESERVED_HOME_NAMES = frozenset(
    {USER_DATA_DIRNAME, USER_SHARES_DIRNAME, USER_CHATS_DIRNAME, AIRD_META_FOLDER}
)
_META_PREFIXES = (USER_SHARES_DIRNAME, USER_CHATS_DIRNAME, AIRD_META_FOLDER)


def _username_from_user(user) -> str:
    if not user:
        return ""
    if isinstance(user, dict):
        return str(user.get("username") or "")
    return str(user)


def _settings(*, root_dir: str | None = None, multi_user: bool | None = None) -> tuple[str, bool]:
    root = constants_module.ROOT_DIR if root_dir is None else root_dir
    mu = constants_module.MULTI_USER if multi_user is None else multi_user
    return root, mu


def _ensure_share_and_chat_dirs(home: str) -> None:
    shares = os.path.join(home, USER_SHARES_DIRNAME)
    chats = os.path.join(home, USER_CHATS_DIRNAME)
    try:
        os.makedirs(shares, exist_ok=True)
        os.makedirs(chats, exist_ok=True)
    except OSError:
        if not os.path.isdir(shares) or not os.path.isdir(chats):
            logger.exception("Could not create chat/share folders under %s", home)


def _migrate_home_entries_into_data(home: str, data: str) -> None:
    try:
        for name in os.listdir(home):
            if name in _RESERVED_HOME_NAMES:
                continue
            src = os.path.join(home, name)
            dest = os.path.join(data, name)
            if os.path.exists(dest):
                continue
            shutil.move(src, dest)
    except OSError:
        logger.exception("Failed to migrate user files into data/ under %s", home)


def user_home_for_username(
    username: str,
    *,
    root_dir: str | None = None,
    multi_user: bool | None = None,
) -> str:
    """Account folder (or ROOT in single-user / token-only mode)."""
    root, mu = _settings(root_dir=root_dir, multi_user=multi_user)
    if not mu:
        return root
    if not username or username in _TOKEN_ONLY_USERNAMES:
        return root
    safe_name = sanitize_username_for_folder(username)
    if not safe_name:
        logger.warning(
            "Cannot create safe folder for username %r, using global root", username
        )
        return root
    home = os.path.join(root, safe_name)
    if not os.path.isdir(home):
        legacy = legacy_folder_name(username)
        if legacy and legacy != safe_name:
            legacy_home = os.path.join(root, legacy)
            if os.path.isdir(legacy_home):
                home = legacy_home
    os.makedirs(home, exist_ok=True)
    return home


def ensure_user_home_layout(
    home: str,
    *,
    root_dir: str | None = None,
    multi_user: bool | None = None,
) -> str:
    """Create shares/chats/data dirs. Return the browsable data directory.

    In multi-user mode, existing files sitting in the account folder are moved
    into ``data/`` (except reserved names).
    """
    root, mu = _settings(root_dir=root_dir, multi_user=multi_user)
    _ensure_share_and_chat_dirs(home)

    if not mu or os.path.normpath(home) == os.path.normpath(root):
        return home

    data = os.path.join(home, USER_DATA_DIRNAME)
    if not os.path.isdir(data):
        os.makedirs(data, exist_ok=True)
        _migrate_home_entries_into_data(home, data)
    else:
        os.makedirs(data, exist_ok=True)
    return data


def user_data_dir_for_username(
    username: str,
    *,
    root_dir: str | None = None,
    multi_user: bool | None = None,
) -> str:
    home = user_home_for_username(
        username, root_dir=root_dir, multi_user=multi_user
    )
    return ensure_user_home_layout(home, root_dir=root_dir, multi_user=multi_user)


def user_home_from_data_root(data_root: str) -> str:
    normalized = os.path.normpath(data_root)
    if os.path.basename(normalized) == USER_DATA_DIRNAME:
        return os.path.dirname(normalized)
    return normalized


def _first_segment(rel: str) -> str:
    return (rel or "").replace("\\", "/").lstrip("/").split("/", 1)[0]


def is_meta_rel(rel: str) -> bool:
    return _first_segment(rel) in _META_PREFIXES


def confine_root_for_rel(data_root: str, rel: str) -> str:
    if is_meta_rel(rel):
        return user_home_from_data_root(data_root)
    return data_root


def join_user_rel(data_root: str, rel: str) -> str:
    """Join a user-relative path, mapping ``.aird-chats`` / ``.aird-shares`` to home."""
    rel_norm = (rel or "").replace("\\", "/").lstrip("/")
    base = confine_root_for_rel(data_root, rel_norm)
    if not rel_norm:
        return os.path.abspath(base)
    return os.path.abspath(os.path.join(base, rel_norm.replace("/", os.sep)))
