"""Collect files and zip/unzip OneDrive backups under the user home."""

from __future__ import annotations

import io
import os
import subprocess
import zipfile
from datetime import datetime, timezone
from pathlib import Path

from aird.constants import AIRD_META_FOLDER
from aird.core.security import is_within_root
from aird.core.user_storage import user_data_dir_for_username, user_home_for_username

BACKUP_FOLDER = "AirdBackup"
MAX_FILES = 8000
MAX_BYTES = 2 * 1024 * 1024 * 1024
SKIP_DIR_NAMES = {".git", "__pycache__", "node_modules"}


def _rel_under(root: str, path: str) -> str | None:
    if not is_within_root(path, root):
        return None
    rel = os.path.relpath(path, root).replace("\\", "/")
    if rel.startswith(".."):
        return None
    return rel


def _git_untracked_under(data_root: str, rel_folder: str = "") -> list[str] | None:
    args = ["git", "-C", data_root, "ls-files", "--others", "--exclude-standard"]
    if rel_folder:
        args.extend(["--", rel_folder.replace("\\", "/")])
    try:
        proc = subprocess.run(
            args,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if proc.returncode != 0:
        return None
    out: list[str] = []
    for line in proc.stdout.splitlines():
        rel = line.strip().replace("\\", "/")
        if rel:
            out.append(rel)
    return out


def _git_untracked(data_root: str) -> list[str]:
    return _git_untracked_under(data_root, "") or []


def _add_path(entries: list[tuple[str, str]], seen: set[str], abs_path: str, arcname: str) -> int:
    if arcname in seen:
        return 0
    seen.add(arcname)
    entries.append((abs_path, arcname))
    return 1


def _walk_file(entries, seen, abs_path, arcname, stats) -> None:
    if stats["files"] >= MAX_FILES or stats["bytes"] >= MAX_BYTES:
        return
    try:
        size = os.path.getsize(abs_path)
    except OSError:
        return
    if stats["bytes"] + size > MAX_BYTES:
        return
    stats["files"] += _add_path(entries, seen, abs_path, arcname)
    stats["bytes"] += size


def _walk_tree(entries, seen, abs_dir: str, arc_prefix: str, stats) -> None:
    try:
        names = os.listdir(abs_dir)
    except OSError:
        return
    for name in names:
        if name in SKIP_DIR_NAMES:
            continue
        child = os.path.join(abs_dir, name)
        arc = f"{arc_prefix}/{name}" if arc_prefix else name
        if os.path.isdir(child) and not os.path.islink(child):
            _walk_tree(entries, seen, child, arc, stats)
        elif os.path.isfile(child) and not os.path.islink(child):
            _walk_file(entries, seen, child, arc, stats)
        if stats["files"] >= MAX_FILES:
            return


def collect_backup_entries(
    username: str,
    *,
    paths: list[str],
    include_untracked: bool,
    include_aird_config: bool,
) -> list[tuple[str, str]]:
    home = user_home_for_username(username)
    data = user_data_dir_for_username(username)
    entries: list[tuple[str, str]] = []
    seen: set[str] = set()
    stats = {"files": 0, "bytes": 0}

    for rel in paths:
        abs_path = os.path.abspath(os.path.join(data, rel.replace("/", os.sep)))
        if not is_within_root(abs_path, data) or not os.path.exists(abs_path):
            continue
        if os.path.isdir(abs_path) and not os.path.islink(abs_path):
            _walk_tree(entries, seen, abs_path, f"files/{rel}", stats)
        elif os.path.isfile(abs_path) and not os.path.islink(abs_path):
            _walk_file(entries, seen, abs_path, f"files/{rel}", stats)

    if include_untracked:
        for rel in _git_untracked(data):
            abs_path = os.path.abspath(os.path.join(data, rel.replace("/", os.sep)))
            if not is_within_root(abs_path, data) or not os.path.isfile(abs_path):
                continue
            _walk_file(entries, seen, abs_path, f"files/{rel}", stats)

    if include_aird_config:
        aird_dir = os.path.join(home, AIRD_META_FOLDER)
        if os.path.isdir(aird_dir):
            _walk_tree(entries, seen, aird_dir, "aird", stats)

    return entries


def build_zip_bytes(entries: list[tuple[str, str]]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for abs_path, arcname in entries:
            try:
                zf.write(abs_path, arcname)
            except OSError:
                continue
    return buf.getvalue()


def backup_filename(username: str) -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    safe = "".join(c for c in username if c.isalnum() or c in ("-", "_")) or "user"
    return f"{safe}-{stamp}.zip"


def extract_zip_bytes(data: bytes, username: str) -> list[str]:
    home = user_home_for_username(username)
    data_root = user_data_dir_for_username(username)
    restored: list[str] = []
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        for info in zf.infolist():
            name = info.filename.replace("\\", "/")
            if info.is_dir() or name.endswith("/"):
                continue
            if ".." in Path(name).parts:
                continue
            if name.startswith("files/"):
                rel = name[len("files/") :]
                dest_root = data_root
            elif name.startswith("aird/"):
                rel = name[len("aird/") :]
                dest_root = os.path.join(home, AIRD_META_FOLDER)
            else:
                continue
            dest = os.path.abspath(os.path.join(dest_root, rel.replace("/", os.sep)))
            if not is_within_root(dest, dest_root):
                continue
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            with zf.open(info) as src, open(dest, "wb") as out:
                out.write(src.read())
            restored.append(name)
    return restored


def _rel_inside(folder_rel: str, file_rel: str) -> str | None:
    folder = (folder_rel or "").replace("\\", "/").strip("/")
    file_rel = file_rel.replace("\\", "/").strip("/")
    if not folder:
        return file_rel
    if file_rel == folder:
        return os.path.basename(file_rel)
    prefix = folder + "/"
    if file_rel.startswith(prefix):
        return file_rel[len(prefix) :]
    return None


def collect_map_entries(
    username: str,
    *,
    local_path: str,
    remote_path: str,
    ignore_extra: str = "",
) -> list[tuple[str, str]]:
    """Untracked files only. Never used to delete remote items."""
    from aird.plugins.onedrive.ignore import matcher_for_folder

    data = user_data_dir_for_username(username)
    folder_rel = (local_path or "").replace("\\", "/").strip("/")
    remote = (remote_path or "").replace("\\", "/").strip("/")
    folder_abs = os.path.abspath(os.path.join(data, folder_rel.replace("/", os.sep))) if folder_rel else data
    if not is_within_root(folder_abs, data) or not os.path.exists(folder_abs):
        return []
    matcher = matcher_for_folder(
        folder_abs if os.path.isdir(folder_abs) else os.path.dirname(folder_abs),
        ignore_extra,
    )
    entries: list[tuple[str, str]] = []
    seen: set[str] = set()
    stats = {"files": 0, "bytes": 0}

    if os.path.isfile(folder_abs) and not os.path.islink(folder_abs):
        inner = os.path.basename(folder_abs)
        if matcher.ignored(inner, is_dir=False):
            return []
        git_rels = _git_untracked_under(data, folder_rel)
        file_rel = folder_rel.replace("\\", "/")
        if git_rels is not None and file_rel not in git_rels:
            return []
        arc = f"{remote}/{inner}" if remote else inner
        _walk_file(entries, seen, folder_abs, arc, stats)
        return entries

    if not os.path.isdir(folder_abs):
        return []

    git_rels = _git_untracked_under(data, folder_rel)
    if git_rels is not None:
        for rel in git_rels:
            inner = _rel_inside(folder_rel, rel)
            if inner is None:
                continue
            if matcher.ignored(inner, is_dir=False):
                continue
            abs_path = os.path.abspath(os.path.join(data, rel.replace("/", os.sep)))
            if not is_within_root(abs_path, folder_abs) or not os.path.isfile(abs_path):
                continue
            arc = f"{remote}/{inner}" if remote else inner
            _walk_file(entries, seen, abs_path, arc, stats)
        return entries

    def walk(abs_dir: str, inner_prefix: str) -> None:
        try:
            names = os.listdir(abs_dir)
        except OSError:
            return
        for name in names:
            if name in SKIP_DIR_NAMES:
                continue
            child = os.path.join(abs_dir, name)
            inner = f"{inner_prefix}/{name}" if inner_prefix else name
            if matcher.ignored(inner, is_dir=os.path.isdir(child)):
                continue
            if os.path.isdir(child) and not os.path.islink(child):
                walk(child, inner)
            elif os.path.isfile(child) and not os.path.islink(child):
                arc = f"{remote}/{inner}" if remote else inner
                _walk_file(entries, seen, child, arc, stats)

    walk(folder_abs, "")
    return entries


def collect_aird_config_entries(username: str) -> list[tuple[str, str]]:
    home = user_home_for_username(username)
    entries: list[tuple[str, str]] = []
    seen: set[str] = set()
    stats = {"files": 0, "bytes": 0}
    aird_dir = os.path.join(home, AIRD_META_FOLDER)
    if os.path.isdir(aird_dir):
        _walk_tree(entries, seen, aird_dir, "aird", stats)
    return entries

