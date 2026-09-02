"""Plugin access mapping and OneDrive backup helpers."""

from __future__ import annotations

import os
import sqlite3
from unittest.mock import patch

import pytest

from aird.db import init_db
from aird.plugins.access import (
    PLUGIN_CHAT,
    PLUGIN_GITLAB,
    PLUGIN_ONEDRIVE,
    PLUGIN_ONEDRIVE_BROWSER,
    SCOPE_ALL,
    SCOPE_NONE,
    SCOPE_USERS,
    get_assignment,
    set_assignment,
    user_may_use_plugin,
)
from aird.plugins.onedrive.backup import collect_backup_entries, extract_zip_bytes, build_zip_bytes
from aird.utils.util import get_files_in_directory


@pytest.fixture
def db():
    conn = sqlite3.connect(":memory:")
    init_db(conn)
    yield conn
    conn.close()


def test_schema_has_plugin_tables(db):
    names = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert "plugin_access" in names
    assert "onedrive_backup_rules" in names
    assert "user_plugin_secrets" in names
    assert "onedrive_sync_state" in names
    assert "onedrive_folder_maps" in names


def test_user_facing_plugins_excludes_admin_apps(db):
    from aird.plugins.access import user_facing_plugins

    with patch("aird.utils.util.is_feature_enabled", return_value=True):
        with patch("aird.plugins.chat.chat_library_available", return_value=True):
            ids = {p["id"] for p in user_facing_plugins("alice", db)}
    assert PLUGIN_CHAT in ids
    assert PLUGIN_ONEDRIVE_BROWSER in ids
    assert PLUGIN_GITLAB not in ids
    assert PLUGIN_ONEDRIVE not in ids


def test_catalog_marks_audience():
    from aird.plugins.access import catalog

    by_id = {p["id"]: p for p in catalog()}
    assert by_id[PLUGIN_GITLAB]["audience"] == "admin"
    assert by_id[PLUGIN_ONEDRIVE]["audience"] == "admin"
    assert by_id[PLUGIN_CHAT]["audience"] == "user"
    assert by_id[PLUGIN_ONEDRIVE_BROWSER]["audience"] == "user"

def test_none_all_users_scopes(db):
    set_assignment(db, PLUGIN_CHAT, SCOPE_NONE)
    assert user_may_use_plugin(PLUGIN_CHAT, "alice", db) is False
    set_assignment(db, PLUGIN_CHAT, SCOPE_ALL)
    assert user_may_use_plugin(PLUGIN_CHAT, "alice", db) is True
    set_assignment(db, PLUGIN_ONEDRIVE, SCOPE_USERS, ["alice", "bob"])
    assert user_may_use_plugin(PLUGIN_ONEDRIVE, "alice", db) is True
    assert user_may_use_plugin(PLUGIN_ONEDRIVE, "carol", db) is False
    assert user_may_use_plugin(PLUGIN_ONEDRIVE, None, db) is False


def test_is_chat_enabled_stays_flag_only():
    from aird.plugins.chat import is_chat_enabled

    with patch("aird.plugins.chat.chat_library_available", return_value=True):
        with patch("aird.utils.util.is_feature_enabled", return_value=True):
            assert is_chat_enabled() is True
        with patch("aird.utils.util.is_feature_enabled", return_value=False):
            assert is_chat_enabled() is False


def test_onedrive_backup_roundtrip(tmp_path):
    data = tmp_path / "alice" / "data"
    data.mkdir(parents=True)
    (data / "notes.txt").write_text("hello", encoding="utf-8")
    aird = tmp_path / "alice" / ".aird"
    aird.mkdir()
    (aird / "config.json").write_text("{}", encoding="utf-8")
    with (
        patch("aird.plugins.onedrive.backup.user_home_for_username", return_value=str(tmp_path / "alice")),
        patch("aird.plugins.onedrive.backup.user_data_dir_for_username", return_value=str(data)),
    ):
        entries = collect_backup_entries(
            "alice",
            paths=["notes.txt"],
            include_untracked=False,
            include_aird_config=True,
        )
        names = {arc for _, arc in entries}
        assert "files/notes.txt" in names
        assert "aird/config.json" in names
        blob = build_zip_bytes(entries)
        dest = tmp_path / "restore" / "alice"
        dest_data = dest / "data"
        dest_data.mkdir(parents=True)
        with (
            patch("aird.plugins.onedrive.backup.user_home_for_username", return_value=str(dest)),
            patch("aird.plugins.onedrive.backup.user_data_dir_for_username", return_value=str(dest_data)),
        ):
            restored = extract_zip_bytes(blob, "alice")
        assert restored
        assert (dest_data / "notes.txt").read_text(encoding="utf-8") == "hello"
        assert (dest / ".aird" / "config.json").read_text(encoding="utf-8") == "{}"


def test_aird_folder_hidden_from_listing(tmp_path):
    (tmp_path / "visible.txt").write_text("x", encoding="utf-8")
    (tmp_path / ".aird").mkdir()
    (tmp_path / ".aird-chats").mkdir()
    files = get_files_in_directory(str(tmp_path))
    names = {f["name"] for f in files}
    assert "visible.txt" in names
    assert ".aird" not in names
    assert ".aird-chats" not in names
