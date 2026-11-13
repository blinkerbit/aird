"""Polls, lists, meet ACL and expiry."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from aird.plugins.collab.acl import VISIBILITY_PRIVATE, VISIBILITY_PUBLIC, can_access
from aird.plugins.collab.lists_db import (
    claim_due_reminders,
    create_group,
    add_item,
    create_list,
    delete_list,
    get_list,
    list_detail,
    list_index,
    patch_item,
    set_list_group,
    update_list,
    user_may_share,
)
from aird.plugins.collab.live import audience_names, notify_changed
from aird.plugins.collab.meet_db import (
    MEET_TTL_HOURS,
    claim_session_start,
    create_meet,
    delete_meet,
    end_meet,
    get_meet,
    meet_join_allowed,
)
from aird.plugins.collab.polls_db import cast_vote, create_poll, delete_poll, get_poll, poll_detail, update_poll
from aird.plugins.collab.refs import link_ref, list_refs, suggest
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


def test_shared_list_items_can_be_checked(collab_db):
    public = create_list(
        collab_db,
        organizer="alice",
        title="Groceries",
        kind="todo",
        visibility="public",
        items=[{"text": "Milk"}],
    )
    item_id = list_detail(collab_db, public["id"], "bob")["items"][0]["id"]
    checked = patch_item(collab_db, public["id"], item_id, "bob", done=True)
    assert checked["items"][0]["done"] is True
    private = create_list(
        collab_db,
        organizer="alice",
        title="Home",
        kind="todo",
        visibility="private",
        items=[{"text": "Keys"}],
        participants=["bob"],
    )
    private_item = list_detail(collab_db, private["id"], "bob")["items"][0]["id"]
    assert patch_item(collab_db, private["id"], private_item, "bob", done=True)["items"][0]["done"] is True
    assert patch_item(collab_db, private["id"], private_item, "carol", done=False) is None


def test_public_list_change_reaches_open_clients(collab_db, monkeypatch):
    sent = {}

    class Hub:
        def connected_user_ids(self):
            return {4, 9}

        def username_for(self, user_id):
            return {4: "alice", 9: "bob"}.get(user_id)

        def broadcast_user_ids(self, ids, payload):
            sent["ids"] = set(ids)
            sent["payload"] = payload

    monkeypatch.setattr("aird.plugins.chat.service.get_chat_hub", lambda: Hub())
    lst = create_list(
        collab_db,
        organizer="alice",
        title="Groceries",
        kind="todo",
        visibility="public",
        items=[{"text": "Milk"}],
    )
    notify_changed(collab_db, "list", lst["id"])
    assert sent["ids"] == {4, 9}
    assert sent["payload"]["type"] == "collab_update"
    assert sent["payload"]["kind"] == "list"
    assert sent["payload"]["id"] == lst["id"]


def test_list_audience_is_shared_people(collab_db):
    lst = create_list(
        collab_db,
        organizer="alice",
        title="Groceries",
        kind="todo",
        visibility="private",
        items=[{"text": "Milk"}],
        participants=["bob"],
    )
    assert audience_names(collab_db, "list", lst["id"]) == {"alice", "bob"}


def test_meet_first_join_starts_again_after_end(collab_db):
    meet = create_meet(collab_db, organizer="alice", title="Standup", visibility="public")
    assert claim_session_start(collab_db, meet["id"], "alice") is True
    assert claim_session_start(collab_db, meet["id"], "bob") is False
    assert end_meet(collab_db, meet["id"], "alice")
    assert claim_session_start(collab_db, meet["id"], "bob") is True
    ok, _reason = meet_join_allowed(collab_db, meet["id"], "carol")
    assert ok


def test_list_notes_groups_and_one_reminder(collab_db):
    past = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
    lst = create_list(
        collab_db,
        organizer="alice",
        title="Groceries",
        kind="todo",
        visibility="public",
        notes="Bring bags",
        items=[{"text": "Milk", "notes": "Oat", "due_at": past}],
    )
    detail = list_detail(collab_db, lst["id"], "alice")
    assert detail["notes"] == "Bring bags"
    assert detail["items"][0]["notes"] == "Oat"
    home = create_group(collab_db, "alice", "Home")
    family = create_group(collab_db, "bob", "Family")
    assert set_list_group(collab_db, lst["id"], "bob", home["id"]) is False
    assert set_list_group(collab_db, lst["id"], "alice", home["id"])
    assert set_list_group(collab_db, lst["id"], "bob", family["id"])
    alice = list_index(collab_db, "alice")["lists"][0]["group_id"]
    bob = list_index(collab_db, "bob")["lists"][0]["group_id"]
    assert alice == home["id"]
    assert bob == family["id"]
    assert len(claim_due_reminders(collab_db)) == 1
    assert claim_due_reminders(collab_db) == []


def test_list_forwarding_is_per_list_and_author_only(collab_db):
    lst = create_list(
        collab_db,
        organizer="alice",
        title="Trip",
        kind="todo",
        visibility="private",
        items=[{"text": "Pack"}],
        participants=["bob"],
    )
    assert lst["allow_forward"] is False
    assert lst["organizer"] == "alice"
    assert user_may_share(collab_db, lst["id"], "alice")
    assert not user_may_share(collab_db, lst["id"], "bob")
    assert update_list(collab_db, lst["id"], "bob", allow_forward=True) is None
    assert link_ref(
        collab_db,
        conversation_id="conv-bob",
        kind="list",
        username="bob",
        artifact_id=lst["id"],
        member_names=["carol"],
    ) is None
    visible = suggest(collab_db, "bob", "list", "trip")
    assert visible and visible[0]["id"] == lst["id"]
    assert visible[0]["may_share"] is False
    assert list_detail(collab_db, lst["id"], "carol") is None
    turned = update_list(collab_db, lst["id"], "alice", allow_forward=True)
    assert turned["allow_forward"] is True
    assert user_may_share(collab_db, lst["id"], "bob")
    pinned = link_ref(
        collab_db,
        conversation_id="conv-bob",
        kind="list",
        username="bob",
        artifact_id=lst["id"],
        member_names=["carol"],
    )
    assert pinned["id"] == lst["id"]
    assert list_detail(collab_db, lst["id"], "carol") is not None


def test_only_author_can_delete_list_poll_and_meet(collab_db):
    lst = create_list(
        collab_db,
        organizer="alice",
        title="Trip",
        kind="todo",
        visibility="public",
        items=[{"text": "Pack"}],
    )
    poll = create_poll(
        collab_db,
        organizer="alice",
        question="Lunch?",
        visibility="public",
        allow_multi=False,
        options=["Pizza", "Salad"],
    )
    meet = create_meet(collab_db, organizer="alice", title="Standup", visibility="public")
    assert delete_list(collab_db, lst["id"], "bob") is False
    assert delete_poll(collab_db, poll["id"], "bob") is False
    assert delete_meet(collab_db, meet["id"], "bob") is False
    assert get_list(collab_db, lst["id"])
    assert delete_list(collab_db, lst["id"], "alice") is True
    assert delete_poll(collab_db, poll["id"], "alice") is True
    assert delete_meet(collab_db, meet["id"], "alice") is True
    assert get_list(collab_db, lst["id"]) is None
    assert get_poll(collab_db, poll["id"]) is None
    assert get_meet(collab_db, meet["id"]) is None
    assert collab_db.execute("SELECT COUNT(*) FROM list_items WHERE list_id = ?", (lst["id"],)).fetchone()[0] == 0


def test_index_splits_tasks_for_agenda_views(collab_db):
    today = datetime.now(timezone.utc).replace(hour=9, minute=0, second=0, microsecond=0)
    create_list(
        collab_db,
        organizer="alice",
        title="Groceries",
        kind="todo",
        visibility="public",
        items=[
            {"text": "Milk", "due_at": today.isoformat()},
            {"text": "Later", "due_at": (today + timedelta(days=3)).isoformat()},
            {"text": "Someday"},
            {"text": "Bought", "done": True},
        ],
    )
    index = list_index(collab_db, "alice")
    assert {task["text"] for task in index["tasks"]} == {"Milk", "Later", "Someday"}
    assert index["logbook"][0]["text"] == "Bought"
    assert index["logbook"][0]["list_title"] == "Groceries"


def test_journal_starts_with_a_markdown_page(collab_db):
    journal = create_list(
        collab_db,
        organizer="alice",
        title="Days",
        kind="journal",
        visibility="public",
        items=[],
    )
    detail = list_detail(collab_db, journal["id"], "alice")
    assert detail["kind"] == "journal"
    assert detail["active"] is True
    assert len(detail["items"]) == 1
    assert detail["items"][0]["kind"] == "note"
    assert detail["items"][0]["text"].startswith("# ")
    renamed = update_list(collab_db, journal["id"], "alice", kind="notes")
    assert renamed["kind"] == "bullet"


def test_shared_todo_is_writable_until_author_locks(collab_db):
    lst = create_list(
        collab_db,
        organizer="alice",
        title="Veges",
        kind="todo",
        visibility="private",
        items=[{"text": "Peas"}],
        participants=["bob"],
    )
    assert lst["read_only"] is False
    assert add_item(collab_db, lst["id"], "bob", "Carrots")
    locked = update_list(collab_db, lst["id"], "alice", read_only=True)
    assert locked["read_only"] is True
    assert locked["can_edit"] is True
    assert add_item(collab_db, lst["id"], "bob", "More") is None
    detail = list_detail(collab_db, lst["id"], "bob")
    assert detail["can_edit"] is False
    assert add_item(collab_db, lst["id"], "alice", "Still mine")
