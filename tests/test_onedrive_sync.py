"""OneDrive Graph sync, marking, and admin root path."""

from __future__ import annotations

import sqlite3
from unittest.mock import MagicMock, patch

import pytest

from aird.cloud import CloudFile, OneDriveProvider, split_drive_path
from aird.db import init_db
from aird.plugins.access import PLUGIN_ONEDRIVE, SCOPE_ALL, set_assignment
from aird.plugins.onedrive import db as od_db
from aird.plugins.onedrive.settings import (
    DEFAULT_ROOT,
    get_settings,
    normalize_interval_minutes,
    normalize_root_path,
    save_settings,
    snapshots_remote_path,
    user_remote_root,
)
from aird.plugins.onedrive.sync import sync_user


@pytest.fixture
def db():
    conn = sqlite3.connect(":memory:")
    init_db(conn)
    yield conn
    conn.close()


def test_split_drive_path_drops_parent_segments():
    assert split_drive_path("../etc/passwd") == ["etc", "passwd"]
    assert split_drive_path("Aird/alice/files") == ["Aird", "alice", "files"]
    assert split_drive_path("") == []


def test_normalize_root_and_interval():
    assert normalize_root_path("") == DEFAULT_ROOT
    assert normalize_root_path("Company/Aird") == "Company/Aird"
    assert normalize_interval_minutes(1) == 5
    assert normalize_interval_minutes(99999) == 1440
    assert normalize_interval_minutes("nope") == 10


def test_save_and_load_onedrive_settings(db):
    save_settings(db, root_path="Backups/Aird", sync_interval_minutes=20)
    settings = get_settings(db)
    assert settings["root_path"] == "Backups/Aird"
    assert settings["sync_interval_minutes"] == 20
    assert user_remote_root("Backups/Aird", "alice") == "Backups/Aird/alice"
    assert snapshots_remote_path("Aird", "alice") == "Aird/alice/snapshots"


def test_save_folder_map(db):
    item = od_db.save_map(
        db,
        "alice",
        local_path="root_folder",
        remote_path="Work/root_folder",
        ignore_extra="*.tmp\nbuild/",
    )
    assert item["local_path"] == "root_folder"
    assert item["remote_path"] == "Work/root_folder"
    listed = od_db.list_maps(db, "alice")
    assert len(listed) == 1
    od_db.delete_map(db, "alice", item["id"])
    assert od_db.list_maps(db, "alice") == []


def test_sync_skips_unchanged_and_uploads_new(db, tmp_path):
    set_assignment(db, PLUGIN_ONEDRIVE, SCOPE_ALL)
    data = tmp_path / "alice" / "data"
    data.mkdir(parents=True)
    target = data / "notes.txt"
    target.write_text("hello", encoding="utf-8")
    st = target.stat()
    od_db.save_map(db, "alice", local_path="notes.txt", remote_path="inbox")
    uploaded = CloudFile(id="remote1", name="notes.txt", is_dir=False)

    def fake_upload(stream, *, drive_path, size=None, content_type=None, conflict="replace"):
        assert drive_path.endswith("inbox/notes.txt")
        assert conflict == "replace"
        assert stream.read() == b"hello"
        return uploaded
    provider = OneDriveProvider("tok")
    mock_resp = MagicMock()
    mock_resp.status_code = 404
    with patch("aird.cloud.requests.get", return_value=mock_resp):
        assert provider.get_item_by_path("Aird/missing") is None


def test_ensure_folder_path_reuses_existing():
    provider = OneDriveProvider("tok")
    existing = CloudFile(id="abc", name="Aird", is_dir=True)
    with patch.object(provider, "get_item_by_path", return_value=existing):
        assert provider.ensure_folder_path("Aird") is existing


def test_sync_skips_unchanged_and_uploads_new(db, tmp_path):
    set_assignment(db, PLUGIN_ONEDRIVE, SCOPE_ALL)
    data = tmp_path / "alice" / "data"
    data.mkdir(parents=True)
    target = data / "notes.txt"
    target.write_text("hello", encoding="utf-8")
    st = target.stat()
    od_db.save_rules(db, "alice", paths=["notes.txt"], include_untracked=False, include_aird_config=False)
    uploaded = CloudFile(id="remote1", name="notes.txt", is_dir=False)

    def fake_upload(stream, *, drive_path, size=None, content_type=None, conflict="replace"):
        assert drive_path.endswith("files/notes.txt")
        assert conflict == "replace"
        assert stream.read() == b"hello"
        return uploaded

    with (
        patch("aird.plugins.onedrive.sync.user_may_use_plugin", return_value=True),
        patch("aird.plugins.onedrive.sync.load_user_token", return_value="tok"),
        patch("aird.plugins.onedrive.backup.user_home_for_username", return_value=str(tmp_path / "alice")),
        patch("aird.plugins.onedrive.backup.user_data_dir_for_username", return_value=str(data)),
        patch("aird.plugins.onedrive.sync.OneDriveProvider") as mock_cls,
    ):
        provider = MagicMock()
        provider.upload_file_at_path.side_effect = fake_upload
        mock_cls.return_value = provider
        first = sync_user(db, "alice")
        assert first["ok"] is True
        assert first["uploaded"] == 1
        assert first["skipped"] == 0
        second = sync_user(db, "alice")
        assert second["uploaded"] == 0
        assert second["skipped"] == 1
        assert provider.upload_file_at_path.call_count == 1

    state = od_db.get_file_state(db, "alice", "inbox/notes.txt")
    assert state is not None
    assert state[1] == st.st_size
