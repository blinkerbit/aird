"""Tests for the optional direct messages plugin."""

from __future__ import annotations

import os
import sqlite3
import tempfile
from unittest.mock import MagicMock, patch

import pytest

from aird.db import init_db
from aird.db.users import create_user
from aird.plugins.chat import chat_library_available, is_chat_enabled
from aird.plugins.chat.db import (
    create_dm_conversation,
    delete_file_share,
    delete_message,
    find_dm_conversation,
    get_message,
    insert_file_share,
    insert_message,
    list_conversations,
    list_shared_with_me_grouped,
    resolve_user_id,
)
from aird.plugins.chat.files import chat_share_rel_path, copy_to_recipient_share
from aird.plugins.chat.sanitize import sanitize_chat_html


@pytest.fixture
def chat_db():
    conn = sqlite3.connect(":memory:")
    init_db(conn)
    create_user(conn, "alice", "secret", role="user")
    create_user(conn, "bob", "secret", role="user")
    yield conn
    conn.close()


def test_sanitize_strips_script():
    if not chat_library_available():
        pytest.skip("bleach not installed")
    dirty = '<p>hi</p><script>alert(1)</script>'
    clean = sanitize_chat_html(dirty)
    assert "<script" not in clean
    assert "hi" in clean


def test_dm_dedup(chat_db):
    a = resolve_user_id(chat_db, "alice")
    b = resolve_user_id(chat_db, "bob")
    c1 = create_dm_conversation(chat_db, a, b)
    c2 = create_dm_conversation(chat_db, b, a)
    assert c1 == c2
    assert find_dm_conversation(chat_db, a, b) == c1


def test_list_conversations(chat_db):
    a = resolve_user_id(chat_db, "alice")
    b = resolve_user_id(chat_db, "bob")
    conv = create_dm_conversation(chat_db, a, b)
    insert_message(
        chat_db,
        conversation_id=conv,
        sender_id=a,
        msg_type="text",
        body="<p>hello</p>",
    )
    convos = list_conversations(chat_db, b)
    assert len(convos) == 1
    assert convos[0]["peer_username"] == "alice"
    assert convos[0]["unread"] == 1


def test_list_messages_after_id(chat_db):
    from aird.plugins.chat.db import list_messages

    a = resolve_user_id(chat_db, "alice")
    b = resolve_user_id(chat_db, "bob")
    conv = create_dm_conversation(chat_db, a, b)
    m1 = insert_message(
        chat_db, conversation_id=conv, sender_id=a, msg_type="text", body="one"
    )
    insert_message(
        chat_db, conversation_id=conv, sender_id=b, msg_type="text", body="two"
    )
    newer = list_messages(chat_db, conv, after_id=m1["id"])
    assert len(newer) == 1
    assert newer[0]["body"] == "two"


def test_chat_ws_check_origin():
    from unittest.mock import MagicMock, patch

    from aird.plugins.chat.ws import ChatWebSocketHandler

    handler = ChatWebSocketHandler(MagicMock(), MagicMock())
    with patch(
        "aird.plugins.chat.ws.is_valid_websocket_origin", return_value=True
    ) as mock_check:
        assert handler.check_origin("http://localhost:8080") is True
        mock_check.assert_called_once_with(handler, "http://localhost:8080")


def test_copy_to_recipient_share(tmp_path):
    sender_root = tmp_path / "bob"
    recipient_root = tmp_path / "alice"
    sender_root.mkdir()
    recipient_root.mkdir()
    src = sender_root / "report.pdf"
    src.write_bytes(b"pdf-data")
    rel, size = copy_to_recipient_share(
        recipient_root=str(recipient_root),
        sender_username="bob",
        source_abs=str(src),
    )
    assert rel.startswith(".aird-shares/bob/")
    assert size == len(b"pdf-data")
    dest = recipient_root / rel.replace("/", os.sep)
    assert dest.is_file()


def test_chat_share_rel_path():
    rel = chat_share_rel_path("alice", "file.txt")
    assert rel == ".aird-shares/alice/file.txt"


def test_is_chat_enabled_requires_both():
    with patch("aird.plugins.chat.chat_library_available", return_value=True):
        with patch("aird.utils.util.is_feature_enabled", return_value=False):
            assert is_chat_enabled() is False
        with patch("aird.utils.util.is_feature_enabled", return_value=True):
            assert is_chat_enabled() is True
    with patch("aird.plugins.chat.chat_library_available", return_value=False):
        with patch("aird.utils.util.is_feature_enabled", return_value=True):
            assert is_chat_enabled() is False


def test_delete_message(chat_db):
    a = resolve_user_id(chat_db, "alice")
    b = resolve_user_id(chat_db, "bob")
    conv = create_dm_conversation(chat_db, a, b)
    msg = insert_message(
        chat_db,
        conversation_id=conv,
        sender_id=a,
        msg_type="text",
        body="<p>delete me</p>",
    )
    assert delete_message(chat_db, msg["id"], b) is None
    assert delete_message(chat_db, msg["id"], a) is not None
    assert get_message(chat_db, msg["id"]) is None


def test_delete_file_share(chat_db, tmp_path):
    from aird.plugins.chat.db import delete_file_share, get_file_share, insert_file_share, list_shared_with_me_grouped
    from aird.plugins.chat.files import copy_to_recipient_share, delete_recipient_share_file

    a = resolve_user_id(chat_db, "alice")
    b = resolve_user_id(chat_db, "bob")
    conv = create_dm_conversation(chat_db, a, b)
    msg = insert_message(chat_db, conversation_id=conv, sender_id=a, msg_type="file", body="")
    recipient_root = tmp_path / "bob"
    recipient_root.mkdir()
    src = tmp_path / "alice" / "doc.txt"
    src.parent.mkdir()
    src.write_text("hi")
    rel, size = copy_to_recipient_share(
        recipient_root=str(recipient_root),
        sender_username="alice",
        source_abs=str(src),
    )
    insert_file_share(
        chat_db,
        conversation_id=conv,
        message_id=msg["id"],
        sender_id=a,
        recipient_id=b,
        relative_path=rel,
        original_name="doc.txt",
        size_bytes=size,
    )
    groups = list_shared_with_me_grouped(chat_db, b)
    assert len(groups) == 1
    assert groups[0]["sender_username"] == "alice"
    assert len(groups[0]["files"]) == 1
    share_id = groups[0]["files"][0]["id"]
    deleted = delete_file_share(chat_db, share_id, b)
    assert deleted is not None
    delete_recipient_share_file(str(recipient_root), rel)
    assert get_file_share(chat_db, share_id) is None
    assert not (recipient_root / rel.replace("/", os.sep)).exists()


def test_get_files_hides_aird_shares(tmp_path):
    from aird.utils.util import get_files_in_directory

    (tmp_path / ".aird-shares").mkdir()
    (tmp_path / "visible.txt").write_text("x")
    names = [f["name"] for f in get_files_in_directory(str(tmp_path))]
    assert ".aird-shares" not in names
    assert "visible.txt" in names
