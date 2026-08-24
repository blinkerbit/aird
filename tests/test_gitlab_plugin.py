"""Tests for the experimental GitLab plugin."""

from __future__ import annotations

import sqlite3
from unittest.mock import MagicMock, patch

import pytest

from aird.db import init_db
from aird.db.users import create_user
from aird.plugins.gitlab.board_stats import busyness_for_issues, days_in_lane, days_since, hours_for_issue
from aird.plugins.gitlab.paths import extract_file_paths, bind_matches_name
from aird.plugins.gitlab import db as gitlab_db
from aird.plugins.gitlab.acl import PathAccess, resolve_access
from aird.plugins.gitlab.mcp import TOOLS, _handle_rpc


@pytest.fixture
def gl_env(tmp_path):
    conn = sqlite3.connect(":memory:")
    init_db(conn)
    create_user(conn, "alice", "secret", role="user")
    create_user(conn, "bob", "secret", role="user")
    with (
        patch("aird.constants.ROOT_DIR", str(tmp_path)),
        patch("aird.constants.MULTI_USER", True),
    ):
        yield conn
        conn.close()


def test_schema_has_gitlab_tables(gl_env):
    names = {r[0] for r in gl_env.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    assert "gitlab_bindings" in names
    assert "file_comments" in names


def test_extract_file_paths():
    md = "See `src/app.py` and [util](lib/util.py) plus docs/readme.md extra"
    paths = extract_file_paths(md)
    assert "src/app.py" in paths
    assert "lib/util.py" in paths
    assert "docs/readme.md" in paths
    assert not any(p.startswith("http") for p in paths)


def test_bind_matches_name():
    mapped = bind_matches_name(
        ["app.py"],
        ["backend/src/app.py"],
        folder_rel="src",
        repo_prefix="backend",
    )
    assert mapped["app.py"] == ["backend/src/app.py"]


def test_comments_crud(gl_env):
    c = gitlab_db.insert_comment(
        gl_env,
        owner_username="alice",
        file_rel_path="src/app.py",
        author_username="alice",
        body="looks off",
    )
    assert c["id"]
    listed = gitlab_db.list_comments(gl_env, "alice", "src/app.py")
    assert len(listed) == 1
    counts = gitlab_db.comment_counts(gl_env, "alice", "src", ["app.py", "other.py"])
    assert counts["app.py"] == 1
    assert counts["other.py"] == 0
    gitlab_db.update_comment(
        gl_env, c["id"], gitlab_issue_iid=12, gitlab_note_id=99, set_promote=True
    )
    got = gitlab_db.get_comment(gl_env, c["id"])
    assert got["gitlab_note_id"] == 99
    assert gitlab_db.delete_comment(gl_env, c["id"])


def test_binding_walk(gl_env):
    gitlab_db.upsert_binding(
        gl_env,
        owner_username="alice",
        folder_rel_path="repo",
        gitlab_host="https://gitlab.example",
        code_project="team/app",
        issues_project="team/scrum",
        board_iid=3,
        repo_path_prefix="backend",
        updated_by="alice",
    )
    found = gitlab_db.resolve_binding(gl_env, "alice", "repo/src")
    assert found["code_project"] == "team/app"
    assert found["issues_project"] == "team/scrum"


def test_busyness_and_lane():
    issues = [
        {
            "state": "opened",
            "weight": 2,
            "assignees": [{"username": "alice"}],
            "time_stats": {},
        },
        {
            "state": "opened",
            "weight": 1,
            "assignees": [{"username": "bob"}],
            "time_stats": {"time_estimate": 7200},
        },
    ]
    snap = busyness_for_issues(issues, window_days=10, hours_per_day=8, hours_per_weight=8)
    assert snap["capacity_hours"] == 80
    by = {u["username"]: u for u in snap["users"]}
    assert by["alice"]["allocated_hours"] == 16
    assert by["bob"]["allocated_hours"] == 2
    assert hours_for_issue({"time_stats": {"time_estimate": 3600}}) == 1
    assert days_in_lane(
        [{"action": "add", "label": {"name": "Doing"}, "created_at": "2020-01-01T00:00:00Z"}],
        ["Doing"],
        now=__import__("datetime").datetime(2020, 1, 11, tzinfo=__import__("datetime").timezone.utc),
    ) == 10
    assert days_since("2020-01-01T00:00:00Z", now=__import__("datetime").datetime(2020, 1, 3, tzinfo=__import__("datetime").timezone.utc)) == 2


def test_owner_proxy_forbidden_for_share(gl_env):
    from aird.db.shares import insert_share

    insert_share(
        gl_env,
        "sid1",
        "2020-01-01T00:00:00Z",
        ["repo"],
        ["bob"],
        None,
        "static",
        None,
        None,
        None,
        modify_users=None,
        created_by="alice",
    )

    handler = MagicMock()
    handler.db_conn = gl_env
    handler.get_cookie = MagicMock(return_value=None)
    handler.get_secure_cookie = MagicMock(return_value=b'{"username":"bob"}')
    handler.request = MagicMock()

    with patch(
        "aird.plugins.gitlab.acl.get_share_by_id",
        wraps=__import__("aird.db.shares", fromlist=["get_share_by_id"]).get_share_by_id,
    ):
        access = resolve_access(handler, path="repo/app.py", share_id="sid1")
    assert access is not None
    assert access.is_self is False
    assert access.can_write is False
    assert access.can_read is True
    assert access.owner_username == "alice"


def test_self_access_is_writable():
    handler = MagicMock()
    handler.db_conn = None
    with patch(
        "aird.handlers.base_handler.get_username_string_for_db", return_value="alice"
    ):
        access = resolve_access(handler, path="repo", share_id=None)
    assert access == PathAccess(
        owner_username="alice",
        rel_path="repo",
        can_read=True,
        can_write=True,
        is_self=True,
        share_id=None,
    )


def test_mcp_tools_list():
    reply = _handle_rpc({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    names = {t["name"] for t in reply["result"]["tools"]}
    assert names == {t["name"] for t in TOOLS}
    assert "list_file_comments" in names
    assert "assignee_load" in names
