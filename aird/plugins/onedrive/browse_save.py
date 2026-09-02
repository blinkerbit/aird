"""Upload browse-selected paths to the server-configured OneDrive account."""

from __future__ import annotations

import logging
import mimetypes
import os

from aird.cloud import CloudProviderError, OneDriveProvider
from aird.plugins.onedrive.settings import get_settings, normalize_root_path

logger = logging.getLogger(__name__)


def _guess_type(path: str) -> str:
    guessed, _ = mimetypes.guess_type(path)
    return guessed or "application/octet-stream"


def _upload_file(provider: OneDriveProvider, local_path: str, remote_path: str) -> None:
    size = os.path.getsize(local_path)
    with open(local_path, "rb") as stream:
        provider.upload_file_at_path(
            stream,
            drive_path=remote_path,
            size=size,
            content_type=_guess_type(local_path),
            conflict="replace",
        )


def _upload_tree(
    provider: OneDriveProvider,
    local_abs: str,
    remote_base: str,
    *,
    is_dir: bool,
) -> int:
    uploaded = 0
    if not is_dir:
        if not os.path.isfile(local_abs):
            raise FileNotFoundError(local_abs)
        _upload_file(provider, local_abs, remote_base)
        return 1

    if not os.path.isdir(local_abs):
        raise FileNotFoundError(local_abs)

    for root, dirs, files in os.walk(local_abs):
        rel = os.path.relpath(root, local_abs)
        remote_dir = remote_base if rel in (".", "") else f"{remote_base}/{rel.replace(os.sep, '/')}"
        for name in dirs:
            provider.ensure_folder_path(f"{remote_dir}/{name}")
        for name in files:
            local_file = os.path.join(root, name)
            remote_file = f"{remote_dir}/{name}"
            _upload_file(provider, local_file, remote_file)
            uploaded += 1
    return uploaded


def save_browse_items(
    provider: OneDriveProvider,
    *,
    items: list[dict],
    resolve_local,
    remote_root: str | None = None,
    settings_conn=None,
) -> dict:
    """Upload browse items. *resolve_local* maps rel path -> absolute path or raises."""
    base = normalize_root_path(remote_root or get_settings(settings_conn)["root_path"])
    browse_base = f"{base}/browse"
    uploaded = 0
    for item in items:
        rel = str(item.get("path") or "").strip().lstrip("/")
        if not rel or ".." in rel.split("/"):
            raise ValueError(f"Invalid path: {rel!r}")
        local_abs = resolve_local(rel)
        if not local_abs:
            raise FileNotFoundError(rel)
        is_dir = bool(item.get("is_dir") or item.get("isDir"))
        if not is_dir and os.path.isdir(local_abs):
            is_dir = True
        remote_path = f"{browse_base}/{rel}"
        if is_dir:
            provider.ensure_folder_path(remote_path)
            uploaded += _upload_tree(provider, local_abs, remote_path, is_dir=True)
        else:
            parent = "/".join(remote_path.split("/")[:-1])
            if parent:
                provider.ensure_folder_path(parent)
            uploaded += _upload_tree(provider, local_abs, remote_path, is_dir=False)
    return {"ok": True, "uploaded": uploaded, "remote_base": browse_base}
