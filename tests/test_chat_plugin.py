"""Tests for the optional direct messages plugin."""

from __future__ import annotations

import os
import sqlite3
from unittest.mock import MagicMock, patch

import pytest

from aird.db import init_db
from aird.db.users import create_user
from aird.plugins.chat import chat_library_available, is_chat_enabled
from aird.plugins.chat.files import chat_share_rel_path, copy_chat_file_to_both, copy_to_recipient_share
from aird.plugins.chat.mailbox import MAX_PINS, reset_pool
from aird.plugins.chat.sanitize import sanitize_chat_html


@pytest.fixture
def chat_env(tmp_path):
    reset_pool()
    conn = sqlite3.connect(":memory:")
    init_db(conn)
    create_user(conn, "alice", "secret", role="user")
    create_user(conn, "bob", "secret", role="user")
    create_user(conn, "carol", "secret", role="user")
    with (
        patch("aird.constants.ROOT_DIR", str(tmp_path)),
        patch("aird.constants.MULTI_USER", True),
    ):
        yield conn
        reset_pool()
        conn.close()


def test_sanitize_strips_script():
    if not chat_library_available():
        pytest.skip("bleach not installed")
    dirty = "<p>hi</p><script>alert(1)</script>"
    clean = sanitize_chat_html(dirty)
    assert "<script" not in clean
    assert "hi" in clean


def test_dm_dedup(chat_env):
    from aird.plugins.chat import db as chat_db

    a = chat_db.resolve_user_id(chat_env, "alice")
    b = chat_db.resolve_user_id(chat_env, "bob")
    c1 = chat_db.create_dm_conversation(chat_env, a, b)
    c2 = chat_db.create_dm_conversation(chat_env, b, a)
    assert c1 == c2
    assert chat_db.find_dm_conversation("alice", "bob") == c1
    assert chat_db.user_in_conversation("bob", c1)


def test_list_conversations_unread(chat_env):
    from aird.plugins.chat import db as chat_db

    a = chat_db.resolve_user_id(chat_env, "alice")
    b = chat_db.resolve_user_id(chat_env, "bob")
    conv = chat_db.create_dm_conversation(chat_env, a, b)
    chat_db.insert_message(
        chat_env,
        username="alice",
        conversation_id=conv,
        msg_type="text",
        body="<p>hello</p>",
    )
    convos = chat_db.list_conversations(chat_env, "bob")
    assert len(convos) == 1
    assert convos[0]["peer_username"] == "alice"
    assert convos[0]["unread"] == 1
    alice_box = chat_db.list_conversations(chat_env, "alice")
    assert alice_box[0]["unread"] == 0


def test_list_messages_after_id(chat_env):
    from aird.plugins.chat import db as chat_db

    a = chat_db.resolve_user_id(chat_env, "alice")
    b = chat_db.resolve_user_id(chat_env, "bob")
    conv = chat_db.create_dm_conversation(chat_env, a, b)
    m1 = chat_db.insert_message(
        chat_env, username="alice", conversation_id=conv, msg_type="text", body="one"
    )
    chat_db.insert_message(
        chat_env, username="bob", conversation_id=conv, msg_type="text", body="two"
    )
    newer = chat_db.list_messages("alice", conv, after_id=m1["id"])
    assert len(newer) == 1
    assert newer[0]["body"] == "two"
    assert chat_db.get_message("bob", m1["id"])["body"] == "one"


def test_message_fanout_same_uuid(chat_env):
    from aird.plugins.chat import db as chat_db

    a = chat_db.resolve_user_id(chat_env, "alice")
    b = chat_db.resolve_user_id(chat_env, "bob")
    conv = chat_db.create_dm_conversation(chat_env, a, b)
    msg = chat_db.insert_message(
        chat_env, username="alice", conversation_id=conv, msg_type="text", body="hi"
    )
    assert chat_db.get_message("bob", msg["id"])["id"] == msg["id"]


def test_group_of_three(chat_env):
    from aird.plugins.chat import db as chat_db

    conv = chat_db.create_group_conversation(
        chat_env, creator_username="alice", title="Team", member_usernames=["bob", "carol"]
    )
    msg = chat_db.insert_message(
        chat_env, username="alice", conversation_id=conv, msg_type="text", body="hey all"
    )
    for user in ("alice", "bob", "carol"):
        got = chat_db.get_message(user, msg["id"])
        assert got is not None
        assert got["body"] == "hey all"
    listed = chat_db.list_conversations(chat_env, "carol")
    assert listed[0]["kind"] == "group"
    assert listed[0]["title"] == "Team"


def test_attach_is_link_only(chat_env, tmp_path):
    from aird.plugins.chat import db as chat_db
    from aird.plugins.chat.files import attachment_meta, owner_abs_path, store_upload

    a = chat_db.resolve_user_id(chat_env, "alice")
    b = chat_db.resolve_user_id(chat_env, "bob")
    conv = chat_db.create_dm_conversation(chat_env, a, b)
    src = tmp_path / "alice" / "data" / "pic.png"
    src.parent.mkdir(parents=True, exist_ok=True)
    src.write_bytes(b"png-bytes")
    rel, size = store_upload(
        sender_username="alice",
        conversation_id=conv,
        source_abs=str(src),
        original_name="pic.png",
    )
    meta = attachment_meta(
        original_name="pic.png",
        owner_username="alice",
        owner_rel=rel,
        size_bytes=size,
    )
    msg = chat_db.insert_message(
        chat_env,
        username="alice",
        conversation_id=conv,
        msg_type="file",
        body="",
        metadata=meta,
    )
    owner_file = owner_abs_path("alice", rel)
    assert os.path.isfile(owner_file)
    bob_home = tmp_path / "bob"
    leaked = list(bob_home.rglob("pic.png")) if bob_home.exists() else []
    assert leaked == []
    bob_msg = chat_db.get_message("bob", msg["id"])
    assert bob_msg["metadata"]["owner_username"] == "alice"
    assert bob_msg["metadata"]["owner_rel"] == rel


def test_multi_attach_one_message(chat_env, tmp_path):
    from aird.plugins.chat import db as chat_db
    from aird.plugins.chat.files import attachment_meta, pack_attachments

    a = chat_db.resolve_user_id(chat_env, "alice")
    b = chat_db.resolve_user_id(chat_env, "bob")
    conv = chat_db.create_dm_conversation(chat_env, a, b)
    atts = [
        attachment_meta(
            original_name="a.log",
            owner_username="alice",
            owner_rel="a.log",
            size_bytes=1,
        ),
        attachment_meta(
            original_name="b.log",
            owner_username="alice",
            owner_rel="b.log",
            size_bytes=2,
        ),
    ]
    msg = chat_db.insert_message(
        chat_env,
        username="alice",
        conversation_id=conv,
        msg_type="file",
        body="<p>hello</p>",
        metadata=pack_attachments(atts),
    )
    bob_msg = chat_db.get_message("bob", msg["id"])
    packed = bob_msg["metadata"]["attachments"]
    assert len(packed) == 2
    assert packed[0]["original_name"] == "a.log"
    assert packed[1]["original_name"] == "b.log"
    listed = chat_db.list_messages("bob", conv)
    assert len(listed) == 1
    assert listed[0]["body"] == "<p>hello</p>"


def test_save_copy_folder(chat_env, tmp_path):
    from aird.plugins.chat.files import attachment_meta, dir_tree_size, save_copy_to_data

    src = tmp_path / "alice" / "data" / "notes"
    src.mkdir(parents=True)
    (src / "a.txt").write_text("hi")
    (src / "nested").mkdir()
    (src / "nested" / "b.txt").write_text("yo")
    inbox = tmp_path / "bob" / "data" / "inbox"
    inbox.mkdir(parents=True)
    saved, size = save_copy_to_data(
        recipient_username="bob",
        source_abs=str(src),
        original_name="notes",
        dest_dir="inbox",
    )
    dest = tmp_path / "bob" / "data" / saved.replace("/", os.sep)
    assert saved.replace("\\", "/") == "inbox/notes"
    assert dest.is_dir()
    assert (dest / "a.txt").read_text() == "hi"
    assert (dest / "nested" / "b.txt").read_text() == "yo"
    assert size == dir_tree_size(str(dest))
    packed = attachment_meta(
        original_name="notes",
        owner_username="alice",
        owner_rel="notes",
        size_bytes=size,
        media_kind="folder",
    )
    assert packed["media_kind"] == "folder"


def test_save_copy_rejects_escape_and_skips_overwrite(chat_env, tmp_path):
    from aird.plugins.chat.files import save_copy_to_data

    src = tmp_path / "alice" / "data" / "pic.txt"
    src.parent.mkdir(parents=True)
    src.write_text("new")
    bob_data = tmp_path / "bob" / "data"
    bob_data.mkdir(parents=True)
    (bob_data / "pic.txt").write_text("old")
    with pytest.raises(ValueError):
        save_copy_to_data(
            recipient_username="bob",
            source_abs=str(src),
            original_name="pic.txt",
            dest_dir="../alice",
        )
    saved, _ = save_copy_to_data(
        recipient_username="bob",
        source_abs=str(src),
        original_name="pic.txt",
        dest_dir="",
    )
    assert saved.replace("\\", "/") == "pic-2.txt"
    assert (bob_data / "pic.txt").read_text() == "old"
    assert (bob_data / "pic-2.txt").read_text() == "new"


def test_attachment_meta_share_fields():
    from aird.plugins.chat.files import attachment_meta

    meta = attachment_meta(
        original_name="notes",
        owner_username="alice",
        owner_rel="notes",
        size_bytes=10,
        media_kind="folder",
        share_id="sid1",
        share_url="/shared/sid1",
    )
    assert meta["share_id"] == "sid1"
    assert meta["share_url"] == "/shared/sid1"


def test_list_shared_with_me_prefers_share_url(chat_env):
    from aird.plugins.chat import db as chat_db
    from aird.plugins.chat.files import attachment_meta, pack_attachments

    a = chat_db.resolve_user_id(chat_env, "alice")
    b = chat_db.resolve_user_id(chat_env, "bob")
    conv = chat_db.create_dm_conversation(chat_env, a, b)
    atts = [
        attachment_meta(
            original_name="folder",
            owner_username="alice",
            owner_rel="folder",
            size_bytes=99,
            media_kind="folder",
            share_id="abc",
            share_url="/shared/abc",
        ),
        attachment_meta(
            original_name="note.txt",
            owner_username="alice",
            owner_rel="note.txt",
            size_bytes=3,
        ),
    ]
    chat_db.insert_message(
        chat_env,
        username="alice",
        conversation_id=conv,
        msg_type="file",
        metadata=pack_attachments(atts),
    )
    listed = chat_db.list_shared_with_me(chat_env, "bob")
    assert len(listed) == 2
    assert listed[0]["file_url"] == "/shared/abc"
    assert listed[0]["share_url"] == "/shared/abc"
    assert listed[1]["file_url"].endswith("/file?i=1")
    patched = chat_db.patch_attachment_share(
        actor="bob",
        conversation_id=conv,
        message_id=listed[1]["id"],
        index=1,
        share_id="xyz",
        share_url="/shared/xyz",
    )
    assert patched["metadata"]["attachments"][1]["share_url"] == "/shared/xyz"
    assert chat_db.get_message("alice", listed[1]["id"])["metadata"]["attachments"][1]["share_url"] == "/shared/xyz"


def test_try_create_chat_share_skips_uploads():
    from aird.constants import CHAT_STORE_FOLDER
    from aird.plugins.chat.handlers import _try_create_chat_share

    handler = MagicMock()
    sid, url = _try_create_chat_share(
        handler, rel_path=f"{CHAT_STORE_FOLDER}/c/x/a.txt", allowed_users=["bob"]
    )
    assert sid is None and url is None
    sid, url = _try_create_chat_share(handler, rel_path="folder", allowed_users=[])
    assert sid is None and url is None
    handler.check_access.assert_not_called()


def test_save_copy_and_410_after_delete_upload(chat_env, tmp_path):
    from aird.plugins.chat import db as chat_db
    from aird.plugins.chat.files import (
        attachment_meta,
        owner_abs_path,
        save_copy_to_data,
        store_upload,
    )

    a = chat_db.resolve_user_id(chat_env, "alice")
    b = chat_db.resolve_user_id(chat_env, "bob")
    conv = chat_db.create_dm_conversation(chat_env, a, b)
    src = tmp_path / "upload.gif"
    src.write_bytes(b"GIF89a")
    rel, size = store_upload(
        sender_username="alice",
        conversation_id=conv,
        source_abs=str(src),
        original_name="clip.gif",
    )
    msg = chat_db.insert_message(
        chat_env,
        username="alice",
        conversation_id=conv,
        msg_type="gif",
        metadata=attachment_meta(
            original_name="clip.gif",
            owner_username="alice",
            owner_rel=rel,
            size_bytes=size,
        ),
    )
    saved, _ = save_copy_to_data(
        recipient_username="bob",
        source_abs=owner_abs_path("alice", rel),
        original_name="clip.gif",
    )
    chat_db.set_saved_rel("bob", msg["id"], saved)
    bob_copy = tmp_path / "bob" / "data" / saved.replace("/", os.sep)
    assert bob_copy.is_file()
    chat_db.delete_message(actor="alice", conversation_id=conv, message_id=msg["id"])
    assert not os.path.isfile(owner_abs_path("alice", rel))
    assert bob_copy.is_file()


def test_edit_reply_react_pin_mute_search(chat_env):
    from aird.plugins.chat import db as chat_db

    a = chat_db.resolve_user_id(chat_env, "alice")
    b = chat_db.resolve_user_id(chat_env, "bob")
    conv = chat_db.create_dm_conversation(chat_env, a, b)
    msg = chat_db.insert_message(
        chat_env,
        username="alice",
        conversation_id=conv,
        msg_type="text",
        body="hello world",
    )
    edited = chat_db.edit_message(
        actor="alice", conversation_id=conv, message_id=msg["id"], body="hello there"
    )
    assert edited["body"] == "hello there"
    assert edited["edited_at"]
    assert chat_db.get_message("bob", msg["id"])["body"] == "hello there"

    reply = chat_db.insert_message(
        chat_env,
        username="bob",
        conversation_id=conv,
        msg_type="text",
        body="replied",
        reply_to_id=msg["id"],
    )
    assert reply["reply_to_id"] == msg["id"]

    reactions = chat_db.toggle_reaction(
        actor="bob", conversation_id=conv, message_id=msg["id"], emoji="👍"
    )
    assert any(r["username"] == "bob" and r["emoji"] == "👍" for r in reactions)
    assert any(r["username"] == "bob" for r in chat_db.get_message("alice", msg["id"])["reactions"])

    pins = chat_db.pin_message(actor="alice", conversation_id=conv, message_id=msg["id"])
    assert len(pins) == 1
    chat_db.set_muted("bob", conv, True)
    assert chat_db.is_muted("bob", conv)
    assert not chat_db.is_muted("alice", conv)

    hits = chat_db.search_messages("alice", "hello")
    assert any(h["id"] == msg["id"] for h in hits)


def test_pin_cap(chat_env):
    from aird.plugins.chat import db as chat_db

    a = chat_db.resolve_user_id(chat_env, "alice")
    b = chat_db.resolve_user_id(chat_env, "bob")
    conv = chat_db.create_dm_conversation(chat_env, a, b)
    ids = []
    for i in range(MAX_PINS + 1):
        msg = chat_db.insert_message(
            chat_env, username="alice", conversation_id=conv, msg_type="text", body=f"m{i}"
        )
        ids.append(msg["id"])
    for mid in ids[:MAX_PINS]:
        chat_db.pin_message(actor="alice", conversation_id=conv, message_id=mid)
    with pytest.raises(ValueError, match="pinned"):
        chat_db.pin_message(actor="alice", conversation_id=conv, message_id=ids[-1])


def test_forward_same_pointer(chat_env, tmp_path):
    from aird.plugins.chat import db as chat_db
    from aird.plugins.chat.files import attachment_meta, store_upload

    a = chat_db.resolve_user_id(chat_env, "alice")
    b = chat_db.resolve_user_id(chat_env, "bob")
    c = chat_db.resolve_user_id(chat_env, "carol")
    dm_ab = chat_db.create_dm_conversation(chat_env, a, b)
    dm_ac = chat_db.create_dm_conversation(chat_env, a, c)
    src = tmp_path / "doc.txt"
    src.write_text("x")
    rel, size = store_upload(
        sender_username="alice",
        conversation_id=dm_ab,
        source_abs=str(src),
        original_name="doc.txt",
    )
    original = chat_db.insert_message(
        chat_env,
        username="alice",
        conversation_id=dm_ab,
        msg_type="file",
        metadata=attachment_meta(
            original_name="doc.txt",
            owner_username="alice",
            owner_rel=rel,
            size_bytes=size,
        ),
    )
    meta = dict(original["metadata"])
    meta["forwarded_from"] = {"username": "alice", "source_message_id": original["id"]}
    fwd = chat_db.insert_message(
        chat_env,
        username="alice",
        conversation_id=dm_ac,
        msg_type="file",
        metadata=meta,
    )
    assert fwd["metadata"]["owner_rel"] == rel
    assert fwd["id"] != original["id"]
    carol_copy = list((tmp_path / "carol").rglob("doc.txt")) if (tmp_path / "carol").exists() else []
    assert carol_copy == []


def test_mentions_extracted(chat_env):
    from aird.plugins.chat import db as chat_db

    conv = chat_db.create_group_conversation(
        chat_env, creator_username="alice", title="G", member_usernames=["bob"]
    )
    msg = chat_db.insert_message(
        chat_env,
        username="alice",
        conversation_id=conv,
        msg_type="text",
        body="hey @bob please look",
    )
    assert "bob" in msg["mentions"]


def test_chat_ws_check_origin():
    from aird.plugins.chat.ws import ChatWebSocketHandler

    handler = ChatWebSocketHandler(MagicMock(), MagicMock())
    with patch("aird.plugins.chat.ws.is_valid_websocket_origin", return_value=True) as mock_check:
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
    assert rel.startswith(".aird-chats/bob/")
    assert size == len(b"pdf-data")
    dest = recipient_root / rel.replace("/", os.sep)
    assert dest.is_file()


def test_copy_chat_file_to_both(tmp_path):
    sender_home = tmp_path / "alice"
    recipient_home = tmp_path / "bob"
    sender_home.mkdir()
    recipient_home.mkdir()
    src = sender_home / "pic.gif"
    src.write_bytes(b"gif")
    sender_rel, recipient_rel, size = copy_chat_file_to_both(
        sender_home=str(sender_home),
        recipient_home=str(recipient_home),
        sender_username="alice",
        recipient_username="bob",
        source_abs=str(src),
        original_name="pic.gif",
    )
    assert sender_rel.startswith(".aird-chats/bob/")
    assert recipient_rel.startswith(".aird-chats/alice/")
    assert size == 3
    assert (sender_home / sender_rel.replace("/", os.sep)).is_file()
    assert (recipient_home / recipient_rel.replace("/", os.sep)).is_file()


def test_chat_share_rel_path():
    rel = chat_share_rel_path("alice", "file.txt")
    assert rel == ".aird-chats/alice/file.txt"


def test_is_chat_enabled_requires_both():
    with patch("aird.plugins.chat.chat_library_available", return_value=True):
        with patch("aird.utils.util.is_feature_enabled", return_value=False):
            assert is_chat_enabled() is False
        with patch("aird.utils.util.is_feature_enabled", return_value=True):
            assert is_chat_enabled() is True
    with patch("aird.plugins.chat.chat_library_available", return_value=False):
        with patch("aird.utils.util.is_feature_enabled", return_value=True):
            assert is_chat_enabled() is False


def test_delete_message_sender_only(chat_env):
    from aird.plugins.chat import db as chat_db

    a = chat_db.resolve_user_id(chat_env, "alice")
    b = chat_db.resolve_user_id(chat_env, "bob")
    conv = chat_db.create_dm_conversation(chat_env, a, b)
    msg = chat_db.insert_message(
        chat_env, username="alice", conversation_id=conv, msg_type="text", body="x"
    )
    assert chat_db.delete_message(actor="bob", conversation_id=conv, message_id=msg["id"]) is None
    assert chat_db.delete_message(actor="alice", conversation_id=conv, message_id=msg["id"]) is not None
    assert chat_db.get_message("alice", msg["id"]) is None
    assert chat_db.get_message("bob", msg["id"]) is None


def test_get_files_hides_aird_shares(tmp_path):
    from aird.utils.util import get_files_in_directory

    (tmp_path / ".aird-shares").mkdir()
    (tmp_path / ".aird-chats").mkdir()
    (tmp_path / "visible.txt").write_text("x")
    names = [f["name"] for f in get_files_in_directory(str(tmp_path))]
    assert ".aird-shares" not in names
    assert ".aird-chats" not in names
    assert "visible.txt" in names


def test_legacy_migrate(chat_env):
    from aird.plugins.chat import db as chat_db
    from aird.plugins.chat.migrate import migrate_user_mailbox

    a = chat_db.resolve_user_id(chat_env, "alice")
    b = chat_db.resolve_user_id(chat_env, "bob")
    chat_env.execute(
        "INSERT INTO chat_conversations (created_at, updated_at) VALUES ('t','t')"
    )
    old_cid = chat_env.execute("SELECT last_insert_rowid()").fetchone()[0]
    chat_env.execute(
        "INSERT INTO chat_members (conversation_id, user_id) VALUES (?, ?), (?, ?)",
        (old_cid, a, old_cid, b),
    )
    chat_env.execute(
        """
        INSERT INTO chat_messages
            (conversation_id, sender_id, msg_type, body, metadata_json, attachment_path, created_at)
        VALUES (?, ?, 'text', 'legacy-hi', '{}', NULL, 't')
        """,
        (old_cid, a),
    )
    chat_env.commit()
    reset_pool()
    migrate_user_mailbox(chat_env, "alice")
    convos = chat_db.list_conversations(chat_env, "alice")
    assert any("legacy-hi" in (c.get("last_preview") or "") for c in convos)


def _b64(fill: bytes, n: int = 32) -> str:
    import base64

    return base64.urlsafe_b64encode(fill * n).decode("ascii").rstrip("=")


def test_e2e_stores_ciphertext_not_plaintext(chat_env):
    from aird.plugins.chat import db as chat_db
    from aird.plugins.chat.e2e import ENCRYPTED_PREVIEW

    a = chat_db.resolve_user_id(chat_env, "alice")
    b = chat_db.resolve_user_id(chat_env, "bob")
    conv = chat_db.create_dm_conversation(chat_env, a, b)
    payload = {"v": 1, "iv": _b64(b"\x01", 12), "ct": _b64(b"\xab", 40)}
    msg = chat_db.insert_message(
        chat_env,
        username="alice",
        conversation_id=conv,
        msg_type="text",
        body="<p>secret-plaintext</p>",
        metadata={"e2e": payload, "mentions": ["bob"]},
    )
    assert msg["body"] == ""
    assert msg["e2e"] is True
    assert msg["metadata"]["e2e"]["ct"] == payload["ct"]
    assert chat_db.get_message("bob", msg["id"])["body"] == ""
    convos = chat_db.list_conversations(chat_env, "bob")
    assert convos[0]["last_preview"] == ENCRYPTED_PREVIEW
    assert chat_db.search_messages("alice", "secret-plaintext") == []
    hits = chat_db.search_messages("alice", payload["ct"][:12])
    assert all(h["id"] != msg["id"] for h in hits)


def test_e2e_identity_rejects_private_jwk(chat_env):
    from aird.plugins.chat.e2e import get_identity_keys, put_identity_key

    pub = {"kty": "EC", "crv": "P-256", "x": _b64(b"\x11"), "y": _b64(b"\x22")}
    put_identity_key(chat_env, "alice", pub)
    assert get_identity_keys(chat_env, ["alice"])["alice"]["x"] == pub["x"]
    with pytest.raises(ValueError, match="Private"):
        put_identity_key(chat_env, "alice", {**pub, "d": _b64(b"\x33")})


def test_e2e_wraps_fanout(chat_env):
    from aird.plugins.chat import db as chat_db

    a = chat_db.resolve_user_id(chat_env, "alice")
    b = chat_db.resolve_user_id(chat_env, "bob")
    conv = chat_db.create_dm_conversation(chat_env, a, b)
    pub = {"kty": "EC", "crv": "P-256", "x": _b64(b"\x11"), "y": _b64(b"\x22")}
    wrap = {
        "v": 1,
        "kid": _b64(b"k", 16),
        "epk": pub,
        "iv": _b64(b"\x03", 12),
        "ct": _b64(b"\x04", 32),
    }
    stored = chat_db.put_e2e_wraps(
        actor="alice", conversation_id=conv, wraps={"alice": wrap, "bob": wrap}
    )
    assert set(stored) == {"alice", "bob"}
    assert "bob" in chat_db.get_e2e_wraps("bob", conv)


def test_plaintext_messages_still_work(chat_env):
    from aird.plugins.chat import db as chat_db

    a = chat_db.resolve_user_id(chat_env, "alice")
    b = chat_db.resolve_user_id(chat_env, "bob")
    conv = chat_db.create_dm_conversation(chat_env, a, b)
    msg = chat_db.insert_message(
        chat_env, username="alice", conversation_id=conv, msg_type="text", body="<p>visible</p>"
    )
    assert "visible" in msg["body"]
    assert msg["e2e"] is False
    assert any("visible" in (h.get("snippet") or h.get("body") or "") for h in chat_db.search_messages("bob", "visible"))
