"""Per-user tags: ownership, access levels, and grants."""

from __future__ import annotations

import sqlite3

import pytest

from aird.db.schema import init_db
from aird.db import user_tags as user_tags_db
from aird.services.user_tag_access import file_tag_access, poll_tag_access
from aird.plugins.collab.schema import ensure_collab_schema
from aird.plugins.collab import polls_db


@pytest.fixture
def conn(tmp_path):
    db = tmp_path / "t.sqlite3"
    c = sqlite3.connect(db)
    c.row_factory = sqlite3.Row
    init_db(c)
    ensure_collab_schema(c)
    return c


def test_same_name_different_owners(conn):
    a = user_tags_db.create_tag(conn, owner_username="alice", name="family", access="read", members=["bob"])
    b = user_tags_db.create_tag(conn, owner_username="bob", name="family", access="write", members=["carol"])
    assert a and b
    assert a["id"] != b["id"]
    user_tags_db.add_link(conn, a["id"], owner_username="alice", kind="file", resource_key="docs")
    user_tags_db.add_link(conn, b["id"], owner_username="bob", kind="file", resource_key="docs")
    assert file_tag_access(conn, viewer="bob", owner_username="alice", rel_path="docs/x.txt") == "read"
    assert file_tag_access(conn, viewer="carol", owner_username="bob", rel_path="docs/x.txt") == "write"
    assert file_tag_access(conn, viewer="carol", owner_username="alice", rel_path="docs/x.txt") == "none"


def test_simple_tag_grants_nothing(conn):
    tag = user_tags_db.create_tag(conn, owner_username="alice", name="label", access="none", members=["bob"])
    user_tags_db.add_link(conn, tag["id"], owner_username="alice", kind="file", resource_key="a.txt")
    assert file_tag_access(conn, viewer="bob", owner_username="alice", rel_path="a.txt") == "none"


def test_folder_inheritance_and_write_wins(conn):
    read_tag = user_tags_db.create_tag(conn, owner_username="alice", name="r", access="read", members=["bob"])
    write_tag = user_tags_db.create_tag(conn, owner_username="alice", name="w", access="write", members=["bob"])
    user_tags_db.add_link(conn, read_tag["id"], owner_username="alice", kind="file", resource_key="folder/file.txt")
    user_tags_db.add_link(conn, write_tag["id"], owner_username="alice", kind="file", resource_key="folder")
    assert file_tag_access(conn, viewer="bob", owner_username="alice", rel_path="folder/file.txt") == "write"


def test_member_add_grants_linked_poll(conn):
    poll = polls_db.create_poll(
        conn,
        organizer="alice",
        question="Q?",
        visibility="private",
        allow_multi=False,
        options=["a", "b"],
        participants=[],
    )
    tag = user_tags_db.create_tag(conn, owner_username="alice", name="team", access="read", members=[])
    user_tags_db.add_link(conn, tag["id"], owner_username="alice", kind="poll", resource_key=poll["id"])
    assert polls_db.poll_detail(conn, poll["id"], "bob") is None
    user_tags_db.update_tag(conn, tag["id"], owner_username="alice", members=["bob"])
    assert polls_db.poll_detail(conn, poll["id"], "bob") is not None
    assert poll_tag_access(conn, "bob", "alice", poll["id"]) == "read"


def test_non_owner_cannot_attach_tag(conn):
    tag = user_tags_db.create_tag(conn, owner_username="alice", name="mine", access="none")
    assert not user_tags_db.add_link(
        conn, tag["id"], owner_username="bob", kind="file", resource_key="secret.txt"
    )


def test_rename_user_updates_tag_tables(conn):
    tag = user_tags_db.create_tag(conn, owner_username="alice", name="fam", access="read", members=["bob"])
    from aird.db.rename_user import _rename_identity_columns

    with conn:
        _rename_identity_columns(conn, "alice", "alicia")
    row = user_tags_db.get_tag(conn, tag["id"])
    assert row["owner_username"] == "alicia"
    members = conn.execute(
        "SELECT username FROM user_tag_members WHERE tag_id = ?", (tag["id"],)
    ).fetchall()
    assert [m[0] for m in members] == ["bob"]
