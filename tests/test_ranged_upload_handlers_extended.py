"""Extended ranged upload handler tests."""

from __future__ import annotations

import asyncio
import json
import os
from unittest.mock import MagicMock, patch

import pytest

from aird.db import init_db
from aird.db.ranged_uploads import create_session
from aird.handlers.ranged_upload_handlers import (
    RangedUploadChunkHandler,
    RangedUploadStatusHandler,
)
from tests.handler_helpers import _default_services, authenticate, patch_db_conn, prepare_handler


@pytest.fixture
def db_conn():
    import sqlite3

    conn = sqlite3.connect(":memory:")
    init_db(conn)
    yield conn
    conn.close()


def _make_handler(handler_cls, body: bytes = b"", headers=None):
    app = MagicMock()
    app.settings = {"services": _default_services()}
    req = MagicMock()
    req.body = body
    req.headers = headers or {}
    req.arguments = {}
    req.remote_ip = "127.0.0.1"
    req.connection = MagicMock()
    req.connection.context = MagicMock()
    handler = handler_cls(app, req)
    authenticate(handler, username="alice")
    prepare_handler(handler)
    handler.get_display_username = MagicMock(return_value="alice")
    handler.get_cookie = MagicMock(return_value="xsrf")
    req.headers.setdefault("X-XSRFToken", "xsrf")
    return handler


@pytest.mark.asyncio
async def test_chunk_handler_partial_upload(db_conn, temp_dir):
    temp_path = os.path.join(temp_dir, "part.bin")
    with open(temp_path, "wb") as fh:
        fh.write(b"\x00" * 100)
    create_session(
        db_conn,
        session_id="up-1",
        username="alice",
        upload_dir="",
        filename="big.bin",
        temp_path=temp_path,
        total_size=100,
    )
    handler = _make_handler(
        RangedUploadChunkHandler,
        body=b"a" * 50,
        headers={"Content-Range": "bytes 0-49/100", "X-XSRFToken": "xsrf"},
    )
    with patch_db_conn(db_conn), patch.object(
        handler, "require_feature", return_value=True
    ), patch("aird.handlers.ranged_upload_handlers.get_user_root", return_value=temp_dir):
        await handler.put("up-1")
    handler.set_status.assert_called_with(200)
    payload = handler.write.call_args[0][0]
    assert payload["status"] == "chunk_received"


@pytest.mark.asyncio
async def test_chunk_handler_completes_upload(db_conn, temp_dir):
    temp_path = os.path.join(temp_dir, "full.bin")
    with open(temp_path, "wb") as fh:
        fh.write(b"\x00" * 20)
    create_session(
        db_conn,
        session_id="up-2",
        username="alice",
        upload_dir="",
        filename="done.bin",
        temp_path=temp_path,
        total_size=20,
    )
    handler = _make_handler(
        RangedUploadChunkHandler,
        body=b"x" * 20,
        headers={"Content-Range": "bytes 0-19/20", "X-XSRFToken": "xsrf"},
    )
    with patch_db_conn(db_conn), patch.object(
        handler, "require_feature", return_value=True
    ), patch(
        "aird.handlers.ranged_upload_handlers.get_user_root", return_value=temp_dir
    ), patch(
        "aird.handlers.ranged_upload_handlers.finalize_upload_to_disk",
        return_value=(True, 201, "ok"),
    ):
        await handler.put("up-2")
    handler.set_status.assert_called_with(201)
    assert handler.write.call_args[0][0]["status"] == "complete"


@pytest.mark.asyncio
async def test_chunk_handler_errors(db_conn):
    handler = _make_handler(
        RangedUploadChunkHandler,
        body=b"x",
        headers={"X-XSRFToken": "xsrf"},
    )
    with patch_db_conn(db_conn), patch.object(handler, "require_feature", return_value=True):
        await handler.put("missing")
    handler.set_status.assert_called_with(404)


@pytest.mark.asyncio
async def test_status_handler(db_conn, temp_dir):
    temp_path = os.path.join(temp_dir, "stat.bin")
    open(temp_path, "wb").close()
    create_session(
        db_conn,
        session_id="up-3",
        username="alice",
        upload_dir="",
        filename="stat.bin",
        temp_path=temp_path,
        total_size=10,
    )
    handler = _make_handler(RangedUploadStatusHandler)
    with patch_db_conn(db_conn):
        await handler.get("up-3")
    payload = handler.write.call_args[0][0]
    assert payload["upload_id"] == "up-3"
    assert payload["complete"] is False


@pytest.mark.asyncio
async def test_chunk_wrong_user(db_conn, temp_dir):
    temp_path = os.path.join(temp_dir, "part2.bin")
    open(temp_path, "wb").close()
    create_session(
        db_conn,
        session_id="up-4",
        username="bob",
        upload_dir="",
        filename="x.bin",
        temp_path=temp_path,
        total_size=10,
    )
    handler = _make_handler(
        RangedUploadChunkHandler,
        body=b"x" * 5,
        headers={"Content-Range": "bytes 0-4/10", "X-XSRFToken": "xsrf"},
    )
    with patch_db_conn(db_conn), patch.object(handler, "require_feature", return_value=True):
        await handler.put("up-4")
    handler.set_status.assert_called_with(403)


@pytest.mark.asyncio
async def test_chunk_bad_range(db_conn, temp_dir):
    temp_path = os.path.join(temp_dir, "part3.bin")
    open(temp_path, "wb").close()
    create_session(
        db_conn,
        session_id="up-5",
        username="alice",
        upload_dir="",
        filename="x.bin",
        temp_path=temp_path,
        total_size=10,
    )
    handler = _make_handler(
        RangedUploadChunkHandler,
        body=b"x",
        headers={"X-XSRFToken": "xsrf"},
    )
    with patch_db_conn(db_conn), patch.object(handler, "require_feature", return_value=True):
        await handler.put("up-5")
    handler.set_status.assert_called_with(400)


@pytest.mark.asyncio
async def test_chunk_finalize_failure(db_conn, temp_dir):
    temp_path = os.path.join(temp_dir, "part4.bin")
    with open(temp_path, "wb") as fh:
        fh.write(b"\x00" * 5)
    create_session(
        db_conn,
        session_id="up-6",
        username="alice",
        upload_dir="",
        filename="fail.bin",
        temp_path=temp_path,
        total_size=5,
    )
    handler = _make_handler(
        RangedUploadChunkHandler,
        body=b"x" * 5,
        headers={"Content-Range": "bytes 0-4/5", "X-XSRFToken": "xsrf"},
    )
    with patch_db_conn(db_conn), patch.object(
        handler, "require_feature", return_value=True
    ), patch(
        "aird.handlers.ranged_upload_handlers.get_user_root", return_value=temp_dir
    ), patch(
        "aird.handlers.ranged_upload_handlers.finalize_upload_to_disk",
        return_value=(False, 500, "fail"),
    ):
        await handler.put("up-6")
    handler.set_status.assert_called_with(500)


def test_chunk_xsrf_required():
    handler = _make_handler(RangedUploadChunkHandler)
    handler.get_cookie = MagicMock(return_value=None)
    with pytest.raises(Exception):
        handler.check_xsrf_cookie()


@pytest.mark.asyncio
async def test_chunk_body_length_mismatch(db_conn, temp_dir):
    temp_path = os.path.join(temp_dir, "part5.bin")
    open(temp_path, "wb").close()
    create_session(
        db_conn,
        session_id="up-7",
        username="alice",
        upload_dir="",
        filename="x.bin",
        temp_path=temp_path,
        total_size=10,
    )
    handler = _make_handler(
        RangedUploadChunkHandler,
        body=b"short",
        headers={"Content-Range": "bytes 0-9/10", "X-XSRFToken": "xsrf"},
    )
    with patch_db_conn(db_conn), patch.object(handler, "require_feature", return_value=True):
        await handler.put("up-7")
    handler.set_status.assert_called_with(400)


@pytest.mark.asyncio
async def test_status_no_db():
    handler = _make_handler(RangedUploadStatusHandler)
    with patch_db_conn(None):
        await handler.get("x")
    handler.set_status.assert_called_with(500)
