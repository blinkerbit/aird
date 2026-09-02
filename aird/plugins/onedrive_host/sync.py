"""Host OneDrive sync using server token and folder maps."""

from __future__ import annotations

import logging
import os
import sqlite3

from aird.cloud import CloudProviderError, OneDriveProvider
from aird.core.browse_paths import resolve_for_user
from aird.core.user_storage import user_data_dir_for_username
from aird.plugins.onedrive import db as od_db
from aird.plugins.onedrive.backup import collect_aird_config_entries, collect_map_entries
from aird.plugins.onedrive.settings import get_settings, resolve_access_token, user_remote_root
from aird.plugins.onedrive_host import status as host_status
from aird.plugins.onedrive_host.token_store import load_file_token

logger = logging.getLogger(__name__)

HOST_USER = "__host__"


def _resolve_token(conn) -> str | None:
    file_tok = load_file_token()
    if file_tok and file_tok.get("access_token"):
        return file_tok["access_token"]
    return resolve_access_token(conn)


def mapped_local_paths(conn) -> list[str]:
    if conn is None:
        return []
    out: list[str] = []
    rows = conn.execute(
        "SELECT DISTINCT username, local_path FROM onedrive_folder_maps"
    ).fetchall()
    for username, local_path in rows:
        try:
            abs_path, _ = resolve_for_user(
                str(username),
                str(local_path),
                user_data_dir_for_username(str(username)),
                conn,
            )
            if abs_path and os.path.isdir(abs_path):
                out.append(abs_path)
        except Exception:
            continue
    return out


def _entries(conn: sqlite3.Connection) -> list[tuple[str, str, str]]:
    seen: set[str] = set()
    out: list[tuple[str, str, str]] = []
    maps = conn.execute(
        "SELECT username, local_path, remote_path, ignore_extra FROM onedrive_folder_maps"
    ).fetchall()
    for username, local_path, remote_path, ignore_extra in maps:
        for abs_path, arcname in collect_map_entries(
            str(username),
            local_path=str(local_path),
            remote_path=str(remote_path),
            ignore_extra=ignore_extra or "",
        ):
            key = f"{username}:{arcname}"
            if key in seen:
                continue
            seen.add(key)
            out.append((str(username), abs_path, arcname))
    rules = od_db.get_rules(conn, HOST_USER)
    if not rules.get("paths"):
        rules = od_db.get_rules(conn, "admin")
    if rules.get("include_aird_config"):
        for abs_path, arcname in collect_aird_config_entries("admin"):
            key = f"admin:{arcname}"
            if key in seen:
                continue
            seen.add(key)
            out.append(("admin", abs_path, arcname))
    return out


def sync_host(conn: sqlite3.Connection | None) -> dict:
    result = {"ok": False, "uploaded": 0, "skipped": 0, "failed": 0, "error": None}
    if conn is None:
        result["error"] = "Database not available"
        return result
    if host_status.is_paused():
        result["error"] = "Sync is paused"
        result["ok"] = True
        return result
    token = _resolve_token(conn)
    if not token:
        result["error"] = "OneDrive host token is not configured"
        return result
    try:
        from aird.plugins.onedrive.settings import get_settings as od_settings

        settings = od_settings(conn)
        if settings.get("auth_source") == "device" and conn is not None:
            from aird.plugins.onedrive.settings import resolve_access_token

            token = resolve_access_token(conn) or token
    except Exception:
        logger.debug("token refresh", exc_info=True)

    entries = _entries(conn)
    if not entries:
        result["ok"] = True
        return result

    provider = OneDriveProvider(token)
    root_base = get_settings(conn)["root_path"]
    conflict = "replace"
    uploaded = skipped = failed = 0
    last_error = None

    for username, abs_path, arcname in entries:
        host_status.enqueue_paths([arcname])
        try:
            st = os.stat(abs_path)
        except OSError:
            failed += 1
            host_status.finish_path(arcname, ok=False, error="missing")
            continue
        stored = od_db.get_file_state(conn, username, arcname)
        if stored and stored[1] == st.st_size and abs(stored[0] - st.st_mtime) < 1e-6:
            skipped += 1
            host_status.finish_path(arcname, ok=True)
            continue
        remote = f"{user_remote_root(root_base, username)}/{arcname}"
        try:
            with open(abs_path, "rb") as fh:
                uploaded_file = provider.upload_file_at_path(
                    fh,
                    drive_path=remote,
                    size=st.st_size,
                    conflict=conflict if conflict in {"replace", "rename", "fail"} else "replace",
                )
            od_db.set_file_state(
                conn,
                username,
                arcname,
                local_mtime=st.st_mtime,
                local_size=st.st_size,
                remote_item_id=uploaded_file.id,
            )
            uploaded += 1
            host_status.finish_path(arcname, ok=True)
        except (OSError, CloudProviderError) as exc:
            failed += 1
            last_error = str(exc)
            host_status.finish_path(arcname, ok=False, error=last_error)
            logger.warning("host sync failed %s", arcname, exc_info=True)

    result.update(
        ok=failed == 0,
        uploaded=uploaded,
        skipped=skipped,
        failed=failed,
        error=last_error if failed else None,
    )
    od_db.set_run_status(
        conn,
        HOST_USER,
        last_finished_at=od_db._now(),
        last_ok=failed == 0,
        last_error=result["error"],
        uploaded=uploaded,
        skipped=skipped,
        failed=failed,
    )
    return result
