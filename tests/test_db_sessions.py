"""Server-side login session persistence tests."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest

from aird.db import init_db
from aird.db.sessions import (
    SESSION_COOKIE_NAME,
    SESSION_IDLE_TIMEOUT_SECONDS,
    create_session,
    get_session,
    list_sessions_for_user,
    revoke_session_for_user,
)
from aird.handlers.base_handler import _try_cookie_auth
from aird.db.users import create_user


@pytest.fixture
def db_conn():
    import sqlite3

    conn = sqlite3.connect(":memory:")
    init_db(conn)
    yield conn
    conn.close()


def test_create_and_validate_session(db_conn):
    session_id = create_session(
        db_conn,
        username="alice",
        user_role="user",
        ip_address="127.0.0.1",
        user_agent="TestAgent",
    )
    session = get_session(db_conn, session_id)
    assert session is not None
    assert session["username"] == "alice"
    assert session["user_role"] == "user"


def test_idle_timeout_invalidates_session(db_conn):
    session_id = create_session(db_conn, username="alice", user_role="user")
    stale = (
        datetime.now(timezone.utc) - timedelta(seconds=SESSION_IDLE_TIMEOUT_SECONDS + 60)
    ).isoformat().replace("+00:00", "Z")
    db_conn.execute(
        "UPDATE user_sessions SET last_active_at = ? WHERE id = ?",
        (stale, session_id),
    )
    db_conn.commit()
    assert get_session(db_conn, session_id) is None


def test_revoke_session_for_user(db_conn):
    session_id = create_session(db_conn, username="alice", user_role="user")
    assert revoke_session_for_user(db_conn, session_id, "alice") is True
    assert get_session(db_conn, session_id) is None


def test_cookie_auth_requires_server_session(db_conn):
    create_user(db_conn, "alice", "Str0ng!Pass#1", role="user")
    session_id = create_session(db_conn, username="alice", user_role="user")
    handler = MagicMock()
    handler.get_secure_cookie.side_effect = (
        lambda name: session_id.encode() if name == SESSION_COOKIE_NAME else None
    )
    handler.settings = {"db_conn": db_conn}
    user = _try_cookie_auth(handler)
    assert user is not None
    assert user["username"] == "alice"
    assert user["_session_id"] == session_id


def test_cookie_auth_rejects_legacy_user_cookie_only(db_conn):
    create_user(db_conn, "alice", "Str0ng!Pass#1", role="user")
    handler = MagicMock()
    handler.get_secure_cookie.return_value = None
    handler.settings = {"db_conn": db_conn}
    assert _try_cookie_auth(handler) is None


def test_list_sessions_for_user(db_conn):
    first = create_session(db_conn, username="alice", user_role="user")
    second = create_session(db_conn, username="alice", user_role="user")
    sessions = list_sessions_for_user(db_conn, "alice")
    assert {s["id"] for s in sessions} == {first, second}
