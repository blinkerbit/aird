"""Custom upload extension normalization and enforcement."""

import sqlite3

import pytest

import aird.constants as constants
from aird.core.upload_extensions import (
    collect_upload_extensions,
    normalize_upload_extension,
)
from aird.db import init_db
from aird.db.config import load_allowed_extensions, save_allowed_extensions
from aird.handlers.file_op_handlers import _validate_upload_filename


@pytest.fixture
def db_conn():
    conn = sqlite3.connect(":memory:")
    init_db(conn)
    yield conn
    conn.close()


def test_normalize_accepts_dotted_and_bare():
    assert normalize_upload_extension("  .HEIC ") == ".heic"
    assert normalize_upload_extension("psd") == ".psd"


def test_normalize_rejects_paths_and_wildcards():
    assert normalize_upload_extension("../etc") is None
    assert normalize_upload_extension(".tar.gz") is None
    assert normalize_upload_extension("*.png") is None
    assert normalize_upload_extension(".he ic") is None
    assert normalize_upload_extension("") is None


def test_collect_unions_and_dedupes():
    chosen, err = collect_upload_extensions([".txt", ".PDF"], "heic, .txt")
    assert err is None
    assert chosen == {".txt", ".pdf", ".heic"}


def test_collect_rejects_invalid_custom():
    chosen, err = collect_upload_extensions([".txt"], "foo/bar")
    assert chosen == set()
    assert err == "Invalid file extension"


def test_save_load_custom_extension(db_conn):
    save_allowed_extensions(db_conn, {".txt", ".heic"})
    assert load_allowed_extensions(db_conn) == {".txt", ".heic"}


def test_upload_allows_saved_custom_extension(monkeypatch):
    monkeypatch.setitem(constants.UPLOAD_CONFIG, "allow_all_file_types", 0)
    monkeypatch.setattr(constants, "UPLOAD_ALLOWED_EXTENSIONS", {".heic"})
    name, err = _validate_upload_filename("photo.heic")
    assert name == "photo.heic"
    assert err is None
    blocked, blocked_err = _validate_upload_filename("photo.exe")
    assert blocked is None
    assert blocked_err[0] == 415
