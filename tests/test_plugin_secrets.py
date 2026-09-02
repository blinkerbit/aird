"""Encrypted GitLab / OneDrive tokens in SQLite."""

from __future__ import annotations

import sqlite3
from unittest.mock import patch

import pytest

from aird.core.secret_storage import _reset_fernet_cache
from aird.db import init_db
from aird.db.plugin_secrets import (
    SECRET_GITLAB,
    SECRET_ONEDRIVE,
    delete_plugin_secret,
    get_plugin_secret,
    set_plugin_secret,
)
from aird.plugins.gitlab.token import (
    delete_user_token as delete_gitlab_token,
)
from aird.plugins.gitlab.token import (
    load_owner_token,
    save_user_token as save_gitlab_token,
    token_file_for as gitlab_token_file,
)
from aird.plugins.onedrive.token import (
    delete_user_token as delete_onedrive_token,
)
from aird.plugins.onedrive.token import (
    load_user_token,
    save_user_token as save_onedrive_token,
    token_file_for as onedrive_token_file,
)


@pytest.fixture
def db(monkeypatch):
    monkeypatch.setenv("AIRD_SECRETS_KEY", "test-plugin-secret-key")
    monkeypatch.delenv("AIRD_COOKIE_SECRET", raising=False)
    for name in ("GITLAB_TOKEN", "GL_TOKEN", "GITLAB_PRIVATE_TOKEN"):
        monkeypatch.delenv(name, raising=False)
    _reset_fernet_cache()
    conn = sqlite3.connect(":memory:")
    init_db(conn)
    yield conn
    conn.close()
    _reset_fernet_cache()


def test_ciphertext_is_encrypted_not_plaintext(db):
    set_plugin_secret(db, "alice", SECRET_GITLAB, "glpat-secret-value")
    row = db.execute(
        "SELECT ciphertext FROM user_plugin_secrets WHERE username = ? AND secret_key = ?",
        ("alice", SECRET_GITLAB),
    ).fetchone()
    assert row[0].startswith("enc:v1:")
    assert "glpat-secret-value" not in row[0]
    assert get_plugin_secret(db, "alice", SECRET_GITLAB) == "glpat-secret-value"
    assert get_plugin_secret(db, "bob", SECRET_GITLAB) is None


def test_gitlab_save_load_delete(db):
    save_gitlab_token("alice", "glpat-alice", conn=db)
    assert load_owner_token("alice", conn=db) == "glpat-alice"
    delete_gitlab_token("alice", conn=db)
    assert load_owner_token("alice", conn=db) is None


def test_onedrive_save_load_delete(db):
    save_onedrive_token("alice", "od-token", conn=db)
    assert load_user_token("alice", conn=db) == "od-token"
    delete_onedrive_token("alice", conn=db)
    assert load_user_token("alice", conn=db) is None
    assert get_plugin_secret(db, "alice", SECRET_ONEDRIVE) is None


def test_gitlab_migrates_plaintext_file_then_deletes_it(db, tmp_path):
    home = tmp_path / "alice"
    with patch("aird.plugins.gitlab.token.user_home_for_username", return_value=str(home)):
        path = gitlab_token_file("alice")
        path.write_text("glpat-from-file", encoding="utf-8")
        assert load_owner_token("alice", conn=db) == "glpat-from-file"
        assert not path.exists()
        assert get_plugin_secret(db, "alice", SECRET_GITLAB) == "glpat-from-file"


def test_onedrive_migrates_plaintext_file_then_deletes_it(db, tmp_path):
    home = tmp_path / "alice"
    with patch("aird.plugins.onedrive.token.user_home_for_username", return_value=str(home)):
        path = onedrive_token_file("alice")
        path.write_text("od-from-file", encoding="utf-8")
        assert load_user_token("alice", conn=db) == "od-from-file"
        assert not path.exists()
        assert get_plugin_secret(db, "alice", SECRET_ONEDRIVE) == "od-from-file"


def test_delete_plugin_secret_is_idempotent(db):
    delete_plugin_secret(db, "alice", SECRET_GITLAB)
    set_plugin_secret(db, "alice", SECRET_GITLAB, "x")
    delete_plugin_secret(db, "alice", SECRET_GITLAB)
    assert get_plugin_secret(db, "alice", SECRET_GITLAB) is None
