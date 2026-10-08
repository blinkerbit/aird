"""Incremental OneDrive mirror via Microsoft Graph (add-only, never delete)."""

from __future__ import annotations

import logging
import os
import sqlite3

from aird.cloud import CloudProviderError, OneDriveProvider
from aird.plugins.access import PLUGIN_ONEDRIVE, user_may_use_plugin
from aird.plugins.onedrive import db as od_db
from aird.plugins.onedrive import is_onedrive_enabled
from aird.plugins.onedrive.backup import collect_aird_config_entries, collect_map_entries
from aird.plugins.onedrive.settings import get_settings, user_remote_root
from aird.plugins.onedrive.token import load_user_token

logger = logging.getLogger(__name__)


def _same(local_mtime: float, local_size: int, stored: tuple[float, int] | None) -> bool:
    if stored is None:
        return False
    return stored[1] == local_size and abs(stored[0] - local_mtime) < 1e-6


def _entries_for_user(conn: sqlite3.Connection, username: str) -> list[tuple[str, str]]:
    seen: set[str] = set()
    out: list[tuple[str, str]] = []
    for mapping in od_db.list_maps(conn, username):
        for abs_path, arcname in collect_map_entries(
            username,
            local_path=mapping["local_path"],
            remote_path=mapping["remote_path"],
            ignore_extra=mapping.get("ignore_extra") or "",
        ):
            if arcname in seen:
                continue
            seen.add(arcname)
            out.append((abs_path, arcname))
    rules = od_db.get_rules(conn, username)
    if rules.get("include_aird_config"):
        for abs_path, arcname in collect_aird_config_entries(username):
            if arcname in seen:
                continue
            seen.add(arcname)
            out.append((abs_path, arcname))
    return out


def sync_user(conn: sqlite3.Connection | None, username: str) -> dict:
    name = (username or "").strip()
    result = {
        "ok": False,
        "username": name,
        "uploaded": 0,
        "skipped": 0,
        "failed": 0,
        "error": None,
    }
    if conn is None or not name:
        result["error"] = "Database not available"
        return result
    started = od_db._now()
    od_db.set_run_status(conn, name, last_started_at=started)
    if not user_may_use_plugin(PLUGIN_ONEDRIVE, name, conn):
        result["error"] = "OneDrive backup is not assigned to this account."
        od_db.set_run_status(
            conn, name, last_started_at=started, last_finished_at=od_db._now(), last_error=result["error"]
        )
        return result
    token = load_user_token(name, conn=conn)
    if not token:
        result["error"] = "OneDrive token is not configured."
        od_db.set_run_status(
            conn, name, last_started_at=started, last_finished_at=od_db._now(), last_error=result["error"]
        )
        return result
    entries = _entries_for_user(conn, name)
    if not entries:
        result["ok"] = True
        od_db.set_run_status(
            conn,
            name,
            last_started_at=started,
            last_finished_at=od_db._now(),
            last_ok=True,
        )
        return result
    provider = OneDriveProvider(token)
    root = user_remote_root(get_settings(conn)["root_path"], name)
    uploaded = skipped = failed = 0
    last_error = None
    for abs_path, arcname in entries:
        try:
            st = os.stat(abs_path)
        except OSError:
            failed += 1
            continue
        if _same(st.st_mtime, st.st_size, od_db.get_file_state(conn, name, arcname)):
            skipped += 1
            continue
        remote = f"{root}/{arcname}"
        try:
            with open(abs_path, "rb") as fh:
                uploaded_file = provider.upload_file_at_path(
                    fh,
                    drive_path=remote,
                    size=st.st_size,
                    conflict="replace",
                )
            od_db.set_file_state(
                conn,
                name,
                arcname,
                local_mtime=st.st_mtime,
                local_size=st.st_size,
                remote_item_id=uploaded_file.id,
            )
            uploaded += 1
        except (OSError, CloudProviderError) as exc:
            failed += 1
            last_error = str(exc)
            logger.warning("OneDrive sync failed for %s %s", name, arcname, exc_info=True)
    result.update(
        {
            "ok": failed == 0,
            "uploaded": uploaded,
            "skipped": skipped,
            "failed": failed,
            "error": last_error if failed else None,
        }
    )
    od_db.set_run_status(
        conn,
        name,
        last_started_at=started,
        last_finished_at=od_db._now(),
        last_ok=failed == 0,
        last_error=result["error"],
        uploaded=uploaded,
        skipped=skipped,
        failed=failed,
    )
    return result


def sync_all_users(conn: sqlite3.Connection | None) -> list[dict]:
    if conn is None or not is_onedrive_enabled():
        return []
    out = []
    for username in od_db.usernames_with_onedrive_token(conn):
        try:
            out.append(sync_user(conn, username))
        except Exception:
            logger.exception("OneDrive sync crashed for %s", username)
    return out
