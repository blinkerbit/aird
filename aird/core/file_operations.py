"""File operation utilities for scanning, filtering, and cloud file management."""

import os
import re
import shutil
import fnmatch
from aird.constants import ROOT_DIR, CLOUD_SHARE_FOLDER, CLOUD_MANAGER
from aird.core.security import is_within_root
from cloud import CloudProviderError


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
    except (OSError, PermissionError) as e:
        print(f"Error scanning directory {root_path}: {e}")
    
    return all_files


def matches_glob_patterns(file_path: str, patterns: list[str]) -> bool:
    """Check if a file path matches any of the given glob patterns"""
    if not patterns:
        return False
    
    for pattern in patterns:
        if fnmatch.fnmatch(file_path, pattern):
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
        pass


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

    filename = sanitize_cloud_filename(item.get("name") or getattr(download, "name", None) or f"{provider_name}-{file_id}")
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


def configure_cloud_providers(config: dict | None) -> None:
    """Configure cloud providers from config"""
    if not config:
        return

    gd_config = config.get("google_drive")
    if gd_config and gd_config.get("enabled"):
        from cloud import GoogleDriveProvider
        credentials_file = gd_config.get("credentials_file")
        token_file = gd_config.get("token_file")
        if credentials_file:
            try:
                gd = GoogleDriveProvider(credentials_file=credentials_file, token_file=token_file)
                CLOUD_MANAGER.register("google_drive", gd)
                print("✅ Google Drive provider registered successfully")
            except Exception as e:
                print(f"⚠️  Failed to register Google Drive provider: {e}")
        else:
            print("⚠️  Google Drive enabled but missing 'credentials_file'")

    od_config = config.get("onedrive")
    if od_config and od_config.get("enabled"):
        from cloud import OneDriveProvider
        client_id = od_config.get("client_id")
        client_secret = od_config.get("client_secret")
        redirect_uri = od_config.get("redirect_uri")
        token_file = od_config.get("token_file")
        if client_id and redirect_uri:
            try:
                od = OneDriveProvider(
                    client_id=client_id,
                    client_secret=client_secret,
                    redirect_uri=redirect_uri,
                    token_file=token_file
                )
                CLOUD_MANAGER.register("onedrive", od)
                print("✅ OneDrive provider registered successfully")
            except Exception as e:
                print(f"⚠️  Failed to register OneDrive provider: {e}")
        else:
            print("⚠️  OneDrive enabled but missing 'client_id' or 'redirect_uri'")
