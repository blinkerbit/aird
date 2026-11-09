"""Polls, lists, meet ACL and expiry."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from aird.plugins.collab.acl import VISIBILITY_PRIVATE, VISIBILITY_PUBLIC, can_access
from aird.plugins.collab.lists_db import create_list, list_detail
from aird.plugins.collab.meet_db import MEET_TTL_HOURS, create_meet, end_meet, meet_join_allowed
from aird.plugins.collab.polls_db import cast_vote, create_poll, poll_detail, update_poll
from aird.plugins.collab.refs import link_ref, list_refs
from aird.plugins.collab.slugs import slugify
from aird.plugins.collab.schema import ensure_collab_schema


@pytest.fixture
def collab_db(tmp_path):
    path = tmp_path / "collab.db"
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    ensure_collab_schema(conn)
    yield conn
    conn.close()


def test_acl_public_private():
    assert can_access(
        visibility=VISIBILITY_PUBLIC,
        organizer="alice",
        username="bob",
        participants=set(),
    )
    assert not can_access(
        visibility=VISIBILITY_PRIVATE,
        organizer="alice",
        username="bob",
        participants={"carol"},
    )
    assert can_access(
        visibility=VISIBILITY_PRIVATE,
        organizer="alice",
        username="carol",
        participants={"carol"},
    )


def test_poll_private_participant(collab_db):
    poll = create_poll(
        collab_db,
        organizer="alice",
        question="Lunch?",
        visibility="private",
        allow_multi=False,
        options=["Pizza", "Salad"],
        participants=["bob"],
    )
    assert poll_detail(collab_db, poll["id"], "bob")
    assert poll_detail(collab_db, poll["id"], "eve") is None


def test_question_edit_resets_votes(collab_db):
    poll = create_poll(
        collab_db,
        organizer="alice",
        question="Lunch?",
        visibility="public",
        allow_multi=False,
        options=["Pizza", "Salad"],
    )
    option_id = poll_detail(collab_db, poll["id"], "alice")["options"][0]["id"]
    cast_vote(collab_db, poll["id"], "bob", [option_id])
    updated = update_poll(collab_db, poll["id"], "alice", question="Dinner?")
    assert updated["question"] == "Dinner?"
    assert updated["total_votes"] == 0


def test_meet_expiry(collab_db):
    meet = create_meet(collab_db, organizer="alice", title="Standup", visibility="public")
    ok, _ = meet_join_allowed(collab_db, meet["id"], "bob")
    assert ok
    exp = datetime.now(timezone.utc) - timedelta(hours=MEET_TTL_HOURS + 1)
    collab_db.execute(
        "UPDATE meets SET expires_at = ? WHERE id = ?",
        (exp.isoformat(), meet["id"]),
    )
    collab_db.commit()
    ok2, reason = meet_join_allowed(collab_db, meet["id"], "bob")
    assert not ok2
    assert "expired" in reason.lower()


def test_slug_and_conversation_shelf(collab_db):
    poll = create_poll(
        collab_db,
        organizer="alice",
        question="Grocery run",
        visibility="private",
        allow_multi=False,
        options=["Milk", "Bread"],
        participants=[],
        slug="grocery-nov1",
    )
    assert poll["slug"] == "grocery-nov1"
    assert slugify("Grocery").startswith("grocery-")
    pinned = link_ref(
        collab_db,
        conversation_id="conv1",
        kind="poll",
        username="alice",
        artifact_id=poll["id"],
        member_names=["bob"],
    )
    assert pinned["token"] == "$$grocery-nov1"
    assert list_refs(collab_db, "conv1", "bob")
    again = create_poll(
        collab_db,
        organizer="alice",
        question="Grocery run",
        visibility="public",
        allow_multi=False,
        options=["Milk", "Bread"],
        slug="grocery-nov1",
    )
    assert again["slug"] != poll["slug"]


def test_inactive_poll_list_and_meet(collab_db):
    poll = create_poll(
        collab_db,
        organizer="alice",
        question="Lunch?",
        visibility="public",
        allow_multi=False,
        options=["Pizza", "Salad"],
    )
    past = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    collab_db.execute("UPDATE polls SET closes_at = ? WHERE id = ?", (past, poll["id"]))
    collab_db.commit()
    closed = poll_detail(collab_db, poll["id"], "alice")
    assert closed["active"] is False
    assert cast_vote(collab_db, poll["id"], "alice", [closed["options"][0]["id"]]) is None

    done = create_list(
        collab_db,
        organizer="alice",
        title="Groceries",
        kind="todo",
        visibility="public",
        items=[{"text": "Milk", "done": True}],
    )
    assert list_detail(collab_db, done["id"], "alice")["active"] is False
    open_list = create_list(
        collab_db,
        organizer="alice",
        title="Notes",
        kind="bullet",
        visibility="public",
        items=[{"text": "Milk", "done": True}],
    )
    assert list_detail(collab_db, open_list["id"], "alice")["active"] is True

    meet = create_meet(collab_db, organizer="alice", title="Standup", visibility="public")
    pinned = link_ref(
        collab_db,
        conversation_id="conv1",
        kind="meet",
        username="alice",
        artifact_id=meet["id"],
    )
    assert pinned["active"] is True
    assert end_meet(collab_db, meet["id"], "alice")
    shelf = list_refs(collab_db, "conv1", "alice")
    assert shelf[0]["active"] is False
