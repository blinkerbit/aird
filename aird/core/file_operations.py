"""File operation utilities for scanning, filtering, and cloud file management."""

import logging
import os
import re
import shutil
import fnmatch
from aird.constants import ROOT_DIR, CLOUD_SHARE_FOLDER, CLOUD_MANAGER
from aird.core.security import is_within_root
from aird.cloud import CloudProviderError

logger = logging.getLogger(__name__)


def get_all_files_recursive(root_path: str, base_path: str = "") -> list:
    """Recursively get all files in a directory"""
    all_files = []
    try:
        for item in os.listdir(root_path):
            item_path = os.path.join(root_path, item)
            relative_path = os.path.join(base_path, item) if base_path else item

            if os.path.isfile(item_path):
                # It's a file, add it to the list
                all_files.append(relative_path)
            elif os.path.isdir(item_path):
                # It's a directory, recursively scan it
                sub_files = get_all_files_recursive(item_path, relative_path)
                all_files.extend(sub_files)
    except OSError as e:
        print(f"Error scanning directory {root_path}: {e}")

    return all_files


def _glob_pattern_to_regex(pattern: str) -> re.Pattern:
    """Convert a glob pattern with ``**`` support into a compiled regex.

    - ``*``    → any sequence of non-slash characters
    - ``**``   → any sequence of characters including ``/`` (zero or more)
    - ``**/``  → optionally preceded by any path prefix (handles root-level matches)
    - ``/**``  → the entry itself OR anything under it
    - ``?``    → any single non-slash character
    """
    # Replace **/ with a special token so we can emit (prefix/)? in the regex
    # This allows **/*.py to match both root-level foo.py and nested a/b/foo.py
    normalized = pattern.replace("**/", "\x00")
    parts = re.split(r"(\*\*)", normalized)
    regex = ""
    for part in parts:
        if part == "**":
            regex += ".*"
        else:
            # Replace the special token back as an optional path prefix
            tokens = part.split("\x00")
            for i, tok in enumerate(tokens):
                if i > 0:
                    regex += "(.+/)?"  # optional leading path prefix
                escaped = re.escape(tok)
                escaped = escaped.replace(r"\*", "[^/]*")
                escaped = escaped.replace(r"\?", "[^/]")
                regex += escaped
    return re.compile(r"^" + regex + r"$")


def _glob_match(path: str, pattern: str) -> bool:
    """Match *path* against *pattern* with full ``**`` wildcard support.

    Rules:
    - ``**/*.py``  matches any ``.py`` file at any depth, including root level.
    - ``docs/**``  matches everything *inside* docs AND the ``docs`` entry itself.
    - ``docs/``    is normalised to ``docs`` (trailing slash stripped).
    """
    # Normalise trailing slash (e.g. "docs/" → "docs")
    pattern = pattern.rstrip("/")
    if not pattern:
        return False

    if "**" not in pattern:
        return fnmatch.fnmatch(path, pattern)

    # Use regex-based matching for ** patterns
    compiled = _glob_pattern_to_regex(pattern)
    if compiled.match(path):
        return True

    # A directory-scoped pattern like "docs/**" should also tag the "docs"
    # directory entry itself — try the prefix before /**
    if pattern.endswith("/**"):
        prefix = pattern[:-3]  # e.g. "docs"
        if fnmatch.fnmatch(path, prefix):
            return True

    return False


def matches_glob_patterns(file_path: str, patterns: list[str]) -> bool:
    """Check if a file path matches any of the given glob patterns"""
    if not patterns:
        return False

    for pattern in patterns:
        if _glob_match(file_path, pattern):
            return True
    return False


def filter_files_by_patterns(
    files: list[str], allow_list: list[str] = None, avoid_list: list[str] = None
) -> list[str]:
    """Filter files based on allow and avoid glob patterns."""
    if not files:
        return files

    filtered_files = []

    for file_path in files:
        # Check avoid list first (takes priority)
        if avoid_list and matches_glob_patterns(file_path, avoid_list):
            continue

        # Check allow list
        if allow_list:
            if matches_glob_patterns(file_path, allow_list):
                filtered_files.append(file_path)
        else:
            # No allow list means all files are allowed (unless in avoid list)
            filtered_files.append(file_path)

    return filtered_files


def get_tags_for_path(rules: list[dict], rel_path: str) -> list[str]:
    """Return a deduplicated, ordered list of tag names that match *rel_path*.

    *rules* is the list of dicts from ``list_resource_tags``.
    Patterns with a leading ``/`` are normalised before matching.
    """
    if not rules or not rel_path:
        return []
    rel = rel_path.replace("\\", "/").lstrip("/")
    seen: dict[str, None] = {}
    for rule in rules:
        pattern = (rule.get("glob_pattern") or "").lstrip("/")
        tag = rule.get("tag") or ""
        if tag and pattern and _glob_match(rel, pattern):
            seen[tag] = None
    return list(seen)


def _process_walk_entry(root_dir: str, dirpath: str, name: str, is_dir: bool, normalised: list[str]) -> str | None:
    """Return relative path (with trailing '/' for dirs) if it matches patterns, else None."""
    full = os.path.join(dirpath, name)
    rel = os.path.relpath(full, root_dir).replace("\\", "/")
    if not matches_glob_patterns(rel, normalised):
        return None
    return rel + "/" if is_dir else rel


def _walk_and_match(root_dir: str, normalised: list[str], max_files: int) -> list[str]:
    """Walk root_dir and collect paths matching normalised glob patterns."""
    result: list[str] = []
    try:
        for dirpath, dirnames, filenames in os.walk(root_dir):
            for dname in dirnames:
                entry = _process_walk_entry(root_dir, dirpath, dname, True, normalised)
                if entry is not None:
                    result.append(entry)
                    if len(result) >= max_files:
                        return result
            for fname in filenames:
                entry = _process_walk_entry(root_dir, dirpath, fname, False, normalised)
                if entry is not None:
                    result.append(entry)
                    if len(result) >= max_files:
                        return result
    except OSError as exc:
        logger.warning("get_files_by_tag_patterns scan error: %s", exc)
    return result


def get_files_by_tag_patterns(
    patterns: list[str],
    root_dir: str | None = None,
    *,
    max_files: int = 5000,
) -> list[str]:
    """Walk *root_dir* and return relative paths of entries matching any of *patterns*.

    Both files and directories are included when their paths match.
    Paths are normalised to forward-slashes for consistent matching.
    Patterns with a leading '/' are normalised (stripped) so that absolute-style
    patterns like '/.coveragerc' match relative paths like '.coveragerc'.
    Returns at most *max_files* results to guard against unbounded scans.
    """
    if not patterns:
        return []
    if root_dir is None:
        root_dir = ROOT_DIR
    normalised = [p.lstrip("/") for p in patterns]
    return _walk_and_match(root_dir, normalised, max_files)


def cloud_root_dir() -> str:
    """Get the root directory for cloud file storage"""
    return os.path.join(ROOT_DIR, CLOUD_SHARE_FOLDER)


def ensure_share_cloud_dir(share_id: str) -> str:
    """Create and return the cloud directory for a specific share"""
    share_dir = os.path.join(cloud_root_dir(), share_id)
    os.makedirs(share_dir, exist_ok=True)
    return share_dir


def sanitize_cloud_filename(name: str | None) -> str:
    """Sanitize a filename for safe cloud storage"""
    candidate = (name or "cloud_file").strip()
    candidate = candidate.replace(os.sep, "_").replace("/", "_")
    candidate = re.sub(r"[^A-Za-z0-9._-]", "_", candidate)
    candidate = candidate.strip("._")
    if not candidate:
        candidate = "cloud_file"
    return candidate[:128]


def is_cloud_relative_path(share_id: str, relative_path: str) -> bool:
    """Check if a relative path is a cloud file path"""
    normalized = relative_path.replace("\\", "/")
    prefix = f"{CLOUD_SHARE_FOLDER}/{share_id}/"
    return normalized.startswith(prefix)


def remove_cloud_file_if_exists(share_id: str, relative_path: str) -> None:
    """Remove a cloud file if it exists"""
    if not is_cloud_relative_path(share_id, relative_path):
        return
    abs_path = os.path.abspath(os.path.join(ROOT_DIR, relative_path))
    if not is_within_root(abs_path, ROOT_DIR):
        return
    if os.path.isfile(abs_path):
        try:
            os.remove(abs_path)
        except OSError:
            pass
    cleanup_share_cloud_dir_if_empty(share_id)


def cleanup_share_cloud_dir_if_empty(share_id: str) -> None:
    """Remove share cloud directory if empty"""
    share_dir = os.path.join(cloud_root_dir(), share_id)
    try:
        if os.path.isdir(share_dir) and not os.listdir(share_dir):
            shutil.rmtree(share_dir, ignore_errors=True)
    except Exception:
        logger.debug("cleanup_share_cloud_dir_if_empty failed", exc_info=True)


def remove_share_cloud_dir(share_id: str) -> None:
    """Remove entire share cloud directory"""
    if not share_id:
        return
    share_dir = os.path.join(cloud_root_dir(), share_id)
    shutil.rmtree(share_dir, ignore_errors=True)


def download_cloud_item(share_id: str, item: dict) -> str:
    """Download a cloud item and return relative path"""
    provider_name = item.get("provider")
    file_id = item.get("id")
    if not provider_name or not file_id:
        raise CloudProviderError("Invalid cloud file specification")
    if item.get("is_dir"):
        raise CloudProviderError("Cloud folder sharing is not supported")
    provider = CLOUD_MANAGER.get(provider_name)
    if not provider:
        raise CloudProviderError(f"Cloud provider '{provider_name}' is not configured")
    try:
        download = provider.download_file(file_id)
    except CloudProviderError:
        raise
    except Exception as exc:
        raise CloudProviderError(str(exc)) from exc

    filename = sanitize_cloud_filename(
        item.get("name")
        or getattr(download, "name", None)
        or f"{provider_name}-{file_id}"
    )
    share_dir = ensure_share_cloud_dir(share_id)
    base, ext = os.path.splitext(filename)
    candidate = filename
    dest_path = os.path.join(share_dir, candidate)
    counter = 1
    while os.path.exists(dest_path):
        candidate = f"{base}_{counter}{ext}"
        dest_path = os.path.join(share_dir, candidate)
        counter += 1

    with open(dest_path, "wb") as out:
        for chunk in download.iter_chunks():
            out.write(chunk)

    relative_path = os.path.relpath(dest_path, ROOT_DIR).replace("\\", "/")
    return relative_path


def download_cloud_items(share_id: str, items: list[dict]) -> list[str]:
    """Download multiple cloud items and return list of relative paths"""
    relative_paths = []
    for item in items:
        try:
            rel_path = download_cloud_item(share_id, item)
            relative_paths.append(rel_path)
        except CloudProviderError as e:
            print(f"Failed to download cloud item: {e}")
    return relative_paths
