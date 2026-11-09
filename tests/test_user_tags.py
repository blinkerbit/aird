"""Per-user tags: ownership, access levels, and grants."""

from __future__ import annotations

import sqlite3

import pytest

from aird.db.schema import init_db
from aird.db import user_tags as user_tags_db
from aird.services.user_tag_access import file_tag_access, poll_tag_access
from aird.plugins.collab.schema import ensure_collab_schema
from aird.plugins.collab import polls_db, lists_db


def _seed_user(conn: sqlite3.Connection, username: str) -> None:
    from datetime import datetime, timezone

    ts = datetime.now(timezone.utc).isoformat()
    conn.execute(
        "INSERT OR IGNORE INTO users (username, password_hash, role, created_at) VALUES (?, ?, 'user', ?)",
        (username, "x", ts),
    )


@pytest.fixture
def conn(tmp_path):
    db = tmp_path / "t.sqlite3"
    c = sqlite3.connect(db)
    c.row_factory = sqlite3.Row
    init_db(c)
    ensure_collab_schema(c)
    for name in ("alice", "bob", "carol"):
        _seed_user(c, name)
    c.commit()
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
    assert user_tags_db.get_tag(conn, tag["id"])["files"] == ["a.txt"]


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


def test_normalize_members_unknown_user(conn):
    from aird.db.user_tags import MemberValidationError, normalize_members

    assert normalize_members(conn, "alice", ["bob"]) == ["bob"]
    with pytest.raises(MemberValidationError):
        normalize_members(conn, "alice", ["nobody"])


def test_tagged_page_includes_collab_links(conn, tmp_path):
    from aird.db.user_tag_collab import build_tagged_page_context
    from aird.plugins.collab import lists_db

    poll = polls_db.create_poll(
        conn,
        organizer="alice",
        question="Ship Friday?",
        visibility="public",
        allow_multi=False,
        options=["yes", "no"],
        participants=[],
    )
    lst = lists_db.create_list(
        conn,
        organizer="alice",
        title="Packing",
        kind="todo",
        visibility="public",
        items=[{"text": "bag"}],
        participants=[],
    )
    tag = user_tags_db.create_tag(conn, owner_username="alice", name="trip", access="none")
    user_tags_db.add_link(conn, tag["id"], owner_username="alice", kind="poll", resource_key=poll["id"])
    user_tags_db.add_link(conn, tag["id"], owner_username="alice", kind="list", resource_key=lst["id"])
    root = tmp_path / "files"
    root.mkdir()
    (root / "ticket.txt").write_text("x")
    user_tags_db.add_link(conn, tag["id"], owner_username="alice", kind="file", resource_key="ticket.txt")
    ctx = build_tagged_page_context(conn, username="alice", tag_name="trip", data_root=str(root))
    assert ctx["tag_source"] == "user"
    assert ctx["file_count"] == 1
    assert ctx["poll_count"] == 1
    assert ctx["list_count"] == 1
    assert ctx["polls"][0]["title"] == "Ship Friday?"
    assert ctx["lists"][0]["title"] == "Packing"


def test_tags_for_file_works_without_row_factory(tmp_path):
    """Production connections often return tuples, not sqlite3.Row."""
    db = tmp_path / "plain.sqlite3"
    c = sqlite3.connect(db)
    init_db(c)
    ensure_collab_schema(c)
    _seed_user(c, "alice")
    c.commit()
    tag = user_tags_db.create_tag(c, owner_username="alice", name="docs", access="none")
    assert tag
    assert user_tags_db.add_link(
        c, tag["id"], owner_username="alice", kind="file", resource_key="a.txt"
    )
    found = user_tags_db.tags_for_file(c, "alice", "a.txt", viewer="alice")
    assert [t["name"] for t in found] == ["docs"]
    colors = {t["name"]: t["color"] for t in user_tags_db.list_tags_for_owner(c, "alice")}
    assert "docs" in colors
    c.close()


def test_replace_artifact_tag_links(conn):
    from aird.db.user_tag_collab import replace_artifact_tag_links, tag_ids_for_resource

    poll = polls_db.create_poll(
        conn,
        organizer="alice",
        question="Q?",
        visibility="public",
        allow_multi=False,
        options=["a", "b"],
        participants=[],
    )
    t1 = user_tags_db.create_tag(conn, owner_username="alice", name="one", access="none")
    t2 = user_tags_db.create_tag(conn, owner_username="alice", name="two", access="none")
    replace_artifact_tag_links(
        conn,
        owner_username="alice",
        kind="poll",
        resource_key=poll["id"],
        tag_ids=[t1["id"], t2["id"]],
    )
    assert set(tag_ids_for_resource(conn, "alice", "poll", poll["id"])) == {t1["id"], t2["id"]}
    replace_artifact_tag_links(
        conn,
        owner_username="alice",
        kind="poll",
        resource_key=poll["id"],
        tag_ids=[],
    )
    assert tag_ids_for_resource(conn, "alice", "poll", poll["id"]) == []


def test_list_index_includes_tag_chips(conn):
    lst = lists_db.create_list(
        conn,
        organizer="alice",
        title="Trip",
        kind="todo",
        visibility="public",
        items=[{"text": "Pack"}],
    )
    tag = user_tags_db.create_tag(conn, owner_username="alice", name="travel", access="none", members=[])
    user_tags_db.add_link(
        conn,
        tag["id"],
        owner_username="alice",
        kind=user_tags_db.KIND_LIST,
        resource_key=lst["id"],
    )
    idx = lists_db.list_index(conn, "alice")
    row = next(item for item in idx["lists"] if item["id"] == lst["id"])
    assert row["tags"][0]["name"] == "travel"
    assert idx["tag_catalog"][0]["name"] == "travel"
