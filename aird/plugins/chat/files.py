"""Chat attachments: one copy on the sender, pointers for everyone else."""

from __future__ import annotations

import os
import shutil
import uuid
from typing import Any

from aird.constants import CHAT_STORE_FOLDER
from aird.constants.media import IMAGE_VIEWER_EXTENSIONS
from aird.core.security import is_within_root
from aird.core.user_storage import (
    ensure_user_home_layout,
    user_data_dir_for_username,
    user_home_for_username,
    user_home_from_data_root,
)
from aird.core.zip_download import MAX_ZIP_UNCOMPRESSED_BYTES


def media_kind_for_name(filename: str) -> str:
    ext = os.path.splitext(filename or "")[1].lower()
    if ext == ".gif":
        return "gif"
    if ext in IMAGE_VIEWER_EXTENSIONS:
        return "image"
    return "file"


def msg_type_for_name(filename: str) -> str:
    kind = media_kind_for_name(filename)
    return "gif" if kind == "gif" else "file"


def owner_abs_path(owner_username: str, owner_rel: str) -> str:
    rel = (owner_rel or "").replace("\\", "/").lstrip("/")
    if not rel or ".." in rel.split("/"):
        raise ValueError("Invalid attachment path")
    home = user_home_for_username(owner_username)
    ensure_user_home_layout(home)
    if rel.startswith(f"{CHAT_STORE_FOLDER}/"):
        abs_path = os.path.abspath(os.path.join(home, rel.replace("/", os.sep)))
        if not is_within_root(abs_path, home):
            raise ValueError("Path escapes user home")
        return abs_path
    data = user_data_dir_for_username(owner_username)
    abs_path = os.path.abspath(os.path.join(data, rel.replace("/", os.sep)))
    if not is_within_root(abs_path, data):
        raise ValueError("Path escapes user data")
    return abs_path


def attachment_store_root(owner_username: str, owner_rel: str) -> str:
    rel = (owner_rel or "").replace("\\", "/").lstrip("/")
    home = user_home_for_username(owner_username)
    ensure_user_home_layout(home)
    if rel.startswith(f"{CHAT_STORE_FOLDER}/"):
        return home
    return user_data_dir_for_username(owner_username)


def _ignore_symlinks(directory: str, names: list[str]) -> list[str]:
    return [n for n in names if os.path.islink(os.path.join(directory, n))]


def dir_tree_size(abs_path: str, cap: int = MAX_ZIP_UNCOMPRESSED_BYTES) -> int:
    total = 0
    for dirpath, dirnames, filenames in os.walk(abs_path, followlinks=False):
        dirnames[:] = [d for d in dirnames if not os.path.islink(os.path.join(dirpath, d))]
        for fname in filenames:
            full = os.path.join(dirpath, fname)
            if os.path.islink(full) or not os.path.isfile(full):
                continue
            try:
                total += os.path.getsize(full)
            except OSError:
                continue
            if total > cap:
                raise ValueError("Folder is too large")
    return total


def normalize_dest_dir(dest_dir: str | None, data_root: str) -> str:
    """Return a relative folder under *data_root*. Empty string is the workspace root."""
    rel = (dest_dir or "").replace("\\", "/").strip("/")
    if not rel:
        return ""
    parts = [p for p in rel.split("/") if p]
    if not parts or any(p in (".", "..") for p in parts) or parts[0].startswith("."):
        raise ValueError("Invalid destination")
    abs_dir = os.path.abspath(os.path.join(data_root, *parts))
    if not is_within_root(abs_dir, data_root) or not os.path.isdir(abs_dir):
        raise ValueError("Destination folder not found")
    return "/".join(parts)


def unique_dest_path(root: str, rel_path: str) -> tuple[str, str]:
    """Return (absolute_path, relative_path) with collision suffix if needed."""
    rel = rel_path.replace("\\", "/").lstrip("/")
    abs_path = os.path.join(root, rel.replace("/", os.sep))
    if not is_within_root(abs_path, root):
        raise ValueError("Path escapes root")
    if not os.path.exists(abs_path):
        return abs_path, rel
    directory, name = os.path.split(abs_path)
    stem, ext = os.path.splitext(name)
    for n in range(2, 100):
        candidate_name = f"{stem}-{n}{ext}"
        candidate_abs = os.path.join(directory, candidate_name)
        if not os.path.exists(candidate_abs):
            rel_out = os.path.join(os.path.dirname(rel), candidate_name).replace("\\", "/")
            return candidate_abs, rel_out.lstrip("/")
    token = uuid.uuid4().hex[:8]
    candidate_name = f"{stem}-{token}{ext}"
    candidate_abs = os.path.join(directory, candidate_name)
    rel_out = os.path.join(os.path.dirname(rel) or "", candidate_name).replace("\\", "/")
    return candidate_abs, rel_out.lstrip("/")


def store_upload(*, sender_username: str, conversation_id: str, source_abs: str, original_name: str) -> tuple[str, int]:
    """Copy an uploaded file into the sender's ``.aird-chats/c/{conv}/``. Return (owner_rel, size)."""
    if not os.path.isfile(source_abs):
        raise FileNotFoundError("Source file not found")
    name = os.path.basename((original_name or os.path.basename(source_abs)).replace("\\", "/"))
    if not name or name in (".", ".."):
        raise ValueError("Invalid filename")
    home = user_home_for_username(sender_username)
    ensure_user_home_layout(home)
    rel_hint = f"{CHAT_STORE_FOLDER}/c/{conversation_id}/{name}"
    dest_abs, rel = unique_dest_path(home, rel_hint)
    os.makedirs(os.path.dirname(dest_abs), exist_ok=True)
    shutil.copy2(source_abs, dest_abs)
    return rel.replace("\\", "/"), os.path.getsize(dest_abs)


def delete_upload_if_owned(*, owner_username: str, owner_rel: str, conversation_id: str) -> None:
    """Unlink a sender upload stored under ``.aird-chats/c/{conv}/``. Leave browse-tree files."""
    rel = (owner_rel or "").replace("\\", "/").lstrip("/")
    prefix = f"{CHAT_STORE_FOLDER}/c/{conversation_id}/"
    if not rel.startswith(prefix):
        return
    abs_path = owner_abs_path(owner_username, rel)
    if os.path.isfile(abs_path):
        os.remove(abs_path)
    parent = os.path.dirname(abs_path)
    try:
        if os.path.isdir(parent) and not os.listdir(parent):
            os.rmdir(parent)
    except OSError:
        pass


def save_copy_to_data(
    *,
    recipient_username: str,
    source_abs: str,
    original_name: str,
    dest_dir: str = "",
) -> tuple[str, int]:
    """Copy a file or folder into a chosen folder under the recipient's ``data/`` tree."""
    if not os.path.isfile(source_abs) and not os.path.isdir(source_abs):
        raise FileNotFoundError("Source not found")
    name = os.path.basename((original_name or os.path.basename(source_abs.rstrip("/\\"))).replace("\\", "/"))
    if not name or name in (".", ".."):
        raise ValueError("Invalid filename")
    data = user_data_dir_for_username(recipient_username)
    dest_rel = normalize_dest_dir(dest_dir, data)
    rel_hint = f"{dest_rel}/{name}" if dest_rel else name
    dest_abs, rel = unique_dest_path(data, rel_hint)
    parent = os.path.dirname(dest_abs)
    os.makedirs(parent or data, exist_ok=True)
    if os.path.isdir(source_abs):
        shutil.copytree(source_abs, dest_abs, ignore=_ignore_symlinks, symlinks=False)
        return rel.replace("\\", "/"), dir_tree_size(dest_abs)
    shutil.copy2(source_abs, dest_abs)
    return rel.replace("\\", "/"), os.path.getsize(dest_abs)


def attachment_entries(meta: dict[str, Any] | None) -> list[dict[str, Any]]:
    meta = meta or {}
    atts = meta.get("attachments")
    if isinstance(atts, list) and atts:
        return [a for a in atts if isinstance(a, dict) and a.get("owner_rel")]
    if meta.get("owner_rel"):
        return [
            {
                "original_name": meta.get("original_name"),
                "owner_username": meta.get("owner_username"),
                "owner_rel": meta.get("owner_rel"),
                "size_bytes": meta.get("size_bytes"),
                "media_kind": meta.get("media_kind"),
                "saved_rel": meta.get("saved_rel"),
                "share_id": meta.get("share_id"),
                "share_url": meta.get("share_url"),
            }
        ]
    return []


def pack_attachments(atts: list[dict[str, Any]]) -> dict[str, Any]:
    first = dict(atts[0])
    first["attachments"] = atts
    return first


def attachment_meta(
    *,
    original_name: str,
    owner_username: str,
    owner_rel: str,
    size_bytes: int,
    saved_rel: str | None = None,
    forwarded_from: dict[str, Any] | None = None,
    media_kind: str | None = None,
    share_id: str | None = None,
    share_url: str | None = None,
) -> dict[str, Any]:
    meta: dict[str, Any] = {
        "original_name": original_name,
        "owner_username": owner_username,
        "owner_rel": owner_rel.replace("\\", "/"),
        "size_bytes": int(size_bytes),
        "media_kind": media_kind or media_kind_for_name(original_name),
    }
    if saved_rel:
        meta["saved_rel"] = saved_rel
    if forwarded_from:
        meta["forwarded_from"] = forwarded_from
    if share_id:
        meta["share_id"] = share_id
    if share_url:
        meta["share_url"] = share_url
    return meta


# --- backward-compat helpers used by older tests / shares UI ---

def chat_share_rel_path(peer_username: str, filename: str) -> str:
    from aird.core.security import sanitize_username_for_folder

    safe_peer = sanitize_username_for_folder(peer_username)
    if not safe_peer:
        raise ValueError("Invalid peer username")
    base = os.path.basename(filename.replace("\\", "/"))
    if not base or base in (".", ".."):
        raise ValueError("Invalid filename")
    return f"{CHAT_STORE_FOLDER}/{safe_peer}/{base}"


def recipient_home_for_username(username: str) -> str:
    home = user_home_for_username(username)
    ensure_user_home_layout(home)
    return home


def copy_to_recipient_share(
    *,
    recipient_root: str,
    sender_username: str,
    source_abs: str,
    original_name: str | None = None,
) -> tuple[str, int]:
    """Legacy dual-copy helper kept for tests."""
    name = original_name or os.path.basename(source_abs)
    home = user_home_from_data_root(recipient_root)
    ensure_user_home_layout(home)
    rel = chat_share_rel_path(sender_username, name)
    dest_abs, final_rel = unique_dest_path(home, rel)
    os.makedirs(os.path.dirname(dest_abs), exist_ok=True)
    shutil.copy2(source_abs, dest_abs)
    return final_rel, os.path.getsize(dest_abs)


def copy_chat_file_to_both(
    *,
    sender_home: str,
    recipient_home: str,
    sender_username: str,
    recipient_username: str,
    source_abs: str,
    original_name: str | None = None,
) -> tuple[str, str, int]:
    """Legacy dual-copy helper kept for tests."""
    name = original_name or os.path.basename(source_abs)
    sender_home = user_home_from_data_root(sender_home)
    recipient_home = user_home_from_data_root(recipient_home)
    rec_rel, size = copy_to_recipient_share(
        recipient_root=recipient_home,
        sender_username=sender_username,
        source_abs=source_abs,
        original_name=name,
    )
    send_rel, _ = copy_to_recipient_share(
        recipient_root=sender_home,
        sender_username=recipient_username,
        source_abs=source_abs,
        original_name=os.path.basename(rec_rel),
    )
    return send_rel, rec_rel, size


def delete_recipient_share_file(recipient_root: str, relative_path: str) -> None:
    home = user_home_from_data_root(recipient_root)
    rel = relative_path.replace("\\", "/").lstrip("/")
    if not rel.startswith(f"{CHAT_STORE_FOLDER}/"):
        raise ValueError("Not a chat store path")
    abs_path = os.path.join(home, rel.replace("/", os.sep))
    if not is_within_root(abs_path, home):
        raise ValueError("Path escapes user home")
    if os.path.isfile(abs_path):
        os.remove(abs_path)
