"""Tests for OneDrive host sync plugin and browser public config."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from aird.db import init_db
from aird.plugins.onedrive import db as od_db
from aird.plugins.onedrive.settings import save_settings
from aird.plugins.onedrive_browser import settings as browser_settings
from aird.plugins.onedrive_host import status as host_status
from aird.plugins.onedrive_host.sync import HOST_USER, mapped_local_paths, sync_host
from aird.plugins.onedrive_host import token_store


@pytest.fixture
def db():
    conn = sqlite3.connect(":memory:")
    init_db(conn)
    yield conn
    conn.close()


@pytest.fixture(autouse=True)
def reset_host_status():
    host_status.set_paused(False)
    host_status.clear_failed()
    while host_status.pop_queue() is not None:
        pass
    view = host_status.public_view()
    for path in list(view.get("in_progress") or []):
        host_status.finish_path(path, ok=True)
    yield
    host_status.set_paused(False)
    host_status.clear_failed()


def test_host_status_pause_queue_and_retry():
    host_status.set_paused(True)
    assert host_status.is_paused() is True
    host_status.set_paused(False)

    host_status.enqueue_paths(["a.txt", "a.txt", "b.txt"])
    assert host_status.pop_queue() == "a.txt"
    host_status.finish_path("a.txt", ok=False, error="boom")
    assert host_status.pop_queue() == "b.txt"
    host_status.finish_path("b.txt", ok=True)

    view = host_status.public_view()
    assert any(not item["ok"] for item in view["failed"])
    assert any(item["ok"] for item in view["recent"])

    queued = host_status.retry_failed()
    assert "a.txt" in queued
    assert host_status.pop_queue() == "a.txt"


def test_token_store_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setattr(token_store, "token_dir", lambda: tmp_path)
    assert token_store.file_token_configured() is False
    assert token_store.load_file_token() is None

    token_store.save_file_token({"access_token": "tok", "refresh_token": "ref"})
    assert token_store.file_token_configured() is True
    loaded = token_store.load_file_token()
    assert loaded["access_token"] == "tok"

    token_store.clear_file_token()
    assert token_store.file_token_configured() is False


def test_browser_public_config_falls_back_to_host_client_id(db):
    cfg = browser_settings.public_config(db)
    assert cfg["configured"] is False
    assert cfg["client_id"] == ""

    save_settings(
        db,
        client_id="11111111-2222-3333-4444-555555555555",
        tenant="organizations",
        auth_source="device",
    )
    cfg = browser_settings.public_config(db)
    assert cfg["configured"] is True
    assert cfg["client_id"] == "11111111-2222-3333-4444-555555555555"
    assert cfg["tenant"] == "organizations"

    browser_settings.save_settings(
        db,
        client_id="aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
        tenant="common",
    )
    cfg = browser_settings.public_config(db)
    assert cfg["client_id"] == "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
    assert cfg["tenant"] == "common"


def test_browser_settings_reject_invalid_client_id(db):
    saved = browser_settings.save_settings(db, client_id="not-a-guid", tenant="common")
    assert saved["client_id"] == ""
    assert browser_settings.get_settings(None)["tenant"] == "common"


def test_mapped_local_paths_resolves_existing_dirs(db, tmp_path, monkeypatch):
    data = tmp_path / "admin" / "data" / "docs"
    data.mkdir(parents=True)
    od_db.save_map(db, "admin", local_path="docs", remote_path="remote/docs")

    monkeypatch.setattr(
        "aird.plugins.onedrive_host.sync.user_data_dir_for_username",
        lambda username: str(tmp_path / username / "data"),
    )
    monkeypatch.setattr(
        "aird.plugins.onedrive_host.sync.resolve_for_user",
        lambda username, rel, root, conn: (str(Path(root) / rel), root),
    )
    paths = mapped_local_paths(db)
    assert str(data) in paths


def test_sync_host_paused_and_missing_token(db):
    host_status.set_paused(True)
    result = sync_host(db)
    assert result["ok"] is True
    assert result["error"] == "Sync is paused"

    host_status.set_paused(False)
    with patch("aird.plugins.onedrive_host.sync._resolve_token", return_value=None):
        result = sync_host(db)
    assert result["ok"] is False
    assert "token" in (result["error"] or "").lower()


def test_sync_host_uploads_new_file(db, tmp_path, monkeypatch):
    data = tmp_path / "admin" / "data"
    data.mkdir(parents=True)
    target = data / "notes.txt"
    target.write_text("hello", encoding="utf-8")
    arc = "inbox/notes.txt"

    monkeypatch.setattr(
        "aird.plugins.onedrive_host.sync._entries",
        lambda conn: [("admin", str(target), arc)],
    )
    monkeypatch.setattr(
        "aird.plugins.onedrive_host.sync._resolve_token",
        lambda conn: "access-token",
    )

    uploaded = MagicMock(id="item-1")
    provider = MagicMock()
    provider.upload_file_at_path.return_value = uploaded

    with patch("aird.plugins.onedrive_host.sync.OneDriveProvider", return_value=provider):
        result = sync_host(db)

    assert result["uploaded"] == 1
    assert result["failed"] == 0
    assert provider.upload_file_at_path.called
    run = od_db.get_run_status(db, HOST_USER)
    assert run["last_ok"] is True


def test_sync_host_skips_unchanged(db, tmp_path, monkeypatch):
    data = tmp_path / "admin" / "data"
    data.mkdir(parents=True)
    target = data / "notes.txt"
    target.write_text("hello", encoding="utf-8")
    st = target.stat()
    arc = "inbox/notes.txt"
    od_db.set_file_state(
        db,
        "admin",
        arc,
        local_mtime=st.st_mtime,
        local_size=st.st_size,
        remote_item_id="existing",
    )
    monkeypatch.setattr(
        "aird.plugins.onedrive_host.sync._entries",
        lambda conn: [("admin", str(target), arc)],
    )
    monkeypatch.setattr(
        "aird.plugins.onedrive_host.sync._resolve_token",
        lambda conn: "access-token",
    )
    provider = MagicMock()
    with patch("aird.plugins.onedrive_host.sync.OneDriveProvider", return_value=provider):
        result = sync_host(db)
    assert result["skipped"] == 1
    assert result["uploaded"] == 0
    assert not provider.upload_file_at_path.called


def test_register_onedrive_host_routes():
    from aird.plugins.onedrive_host import register_onedrive_host

    routes = []
    register_onedrive_host(routes)
    paths = [pattern for pattern, _handler in routes]
    assert "/api/onedrive-host/status" in paths
    assert "/api/onedrive-host/device-poll" in paths


def test_onedrive_legacy_register_is_noop():
    from aird.plugins.onedrive import is_onedrive_enabled, register_onedrive

    routes = [("existing", object)]
    register_onedrive(routes)
    assert routes == [("existing", object)]
    with patch("aird.utils.util.is_feature_enabled", return_value=True):
        assert is_onedrive_enabled() is True
