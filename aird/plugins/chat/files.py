"""Copy chat-shared files into recipient workspace."""

from __future__ import annotations

import os
import shutil
import uuid

from aird.constants import CHAT_SHARE_FOLDER
from aird.core.security import is_within_root, sanitize_username_for_folder


def chat_share_rel_path(sender_username: str, filename: str) -> str:
    safe_sender = sanitize_username_for_folder(sender_username)
    if not safe_sender:
        raise ValueError("Invalid sender username")
    base = os.path.basename(filename.replace("\\", "/"))
    if not base or base in (".", ".."):
        raise ValueError("Invalid filename")
    return f"{CHAT_SHARE_FOLDER}/{safe_sender}/{base}"


def unique_dest_path(recipient_root: str, rel_path: str) -> tuple[str, str]:
    """Return (absolute_path, relative_path) with collision suffix if needed."""
    abs_path = os.path.join(recipient_root, rel_path.replace("/", os.sep))
    if not is_within_root(abs_path, recipient_root):
        raise ValueError("Path escapes recipient root")
    if not os.path.exists(abs_path):
        return abs_path, rel_path.replace("\\", "/")

    directory, name = os.path.split(abs_path)
    stem, ext = os.path.splitext(name)
    for n in range(2, 100):
        candidate_name = f"{stem}-{n}{ext}"
        candidate_abs = os.path.join(directory, candidate_name)
        if not os.path.exists(candidate_abs):
            rel = os.path.join(os.path.dirname(rel_path), candidate_name).replace("\\", "/")
            return candidate_abs, rel
    token = uuid.uuid4().hex[:8]
    candidate_name = f"{stem}-{token}{ext}"
    candidate_abs = os.path.join(directory, candidate_name)
    rel = os.path.join(os.path.dirname(rel_path), candidate_name).replace("\\", "/")
    return candidate_abs, rel


def recipient_root_for_username(username: str) -> str:
    from aird.handlers.base_handler import get_user_root

    class _Handler:
        def get_current_user(self):
            return {"username": username}

    return get_user_root(_Handler())


def copy_to_recipient_share(
    *,
    recipient_root: str,
    sender_username: str,
    source_abs: str,
    original_name: str | None = None,
) -> tuple[str, int]:
    """Copy file into ``{recipient_root}/.aird-shares/{sender}/``."""
    if not os.path.isfile(source_abs):
        raise FileNotFoundError("Source file not found")
    name = original_name or os.path.basename(source_abs)
    rel = chat_share_rel_path(sender_username, name)
    dest_abs, final_rel = unique_dest_path(recipient_root, rel)
    os.makedirs(os.path.dirname(dest_abs), exist_ok=True)
    shutil.copy2(source_abs, dest_abs)
    return final_rel, os.path.getsize(dest_abs)


def delete_recipient_share_file(recipient_root: str, relative_path: str) -> None:
    """Delete one chat-shared file under the recipient's ``.aird-shares/{sender}/`` tree."""
    rel = relative_path.replace("\\", "/").lstrip("/")
    if not rel.startswith(f"{CHAT_SHARE_FOLDER}/"):
        raise ValueError("Not a chat share path")
    abs_path = os.path.join(recipient_root, rel.replace("/", os.sep))
    if not is_within_root(abs_path, recipient_root):
        raise ValueError("Path escapes recipient root")
    if os.path.isfile(abs_path):
        os.remove(abs_path)
    parent = os.path.dirname(abs_path)
    try:
        if os.path.isdir(parent) and not os.listdir(parent):
            os.rmdir(parent)
        grand = os.path.dirname(parent)
        if (
            os.path.basename(parent) != CHAT_SHARE_FOLDER
            and os.path.isdir(grand)
            and os.path.basename(grand) == CHAT_SHARE_FOLDER
            and not os.listdir(grand)
        ):
            os.rmdir(grand)
    except OSError:
        pass
