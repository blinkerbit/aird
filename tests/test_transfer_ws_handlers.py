import json
import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from aird.handlers.transfer_ws_handlers import FileTransferWebSocketHandler
from tests.handler_helpers import authenticate
from tests.test_api_handlers import make_ws_handler


def make_transfer_ws_handler():
    handler = make_ws_handler(FileTransferWebSocketHandler)
    handler.write_message = AsyncMock()
    return handler


@pytest.mark.asyncio
async def test_open_requires_auth():
    handler = make_transfer_ws_handler()
    with patch.object(handler, "get_current_user", return_value=None):
        await handler.open()
    handler.close.assert_called_once()


@pytest.mark.asyncio
async def test_open_ready_when_authenticated():
    handler = make_transfer_ws_handler()
    authenticate(handler, role="user")
    with patch.object(
        FileTransferWebSocketHandler.connection_manager, "add_connection", return_value=True
    ):
        await handler.open()
    handler.write_message.assert_awaited()
    payload = json.loads(handler.write_message.await_args[0][0])
    assert payload["type"] == "ready"


@pytest.mark.asyncio
async def test_stream_upload(tmp_path):
    handler = make_transfer_ws_handler()
    authenticate(handler, role="user")
    data = b"hello ws stream upload"

    with patch(
        "aird.handlers.transfer_ws_handlers.get_user_root", return_value=str(tmp_path)
    ), patch(
        "aird.handlers.transfer_ws_handlers.is_feature_enabled", return_value=True
    ), patch(
        "aird.handlers.transfer_ws_handlers._ws_check_access", return_value=False
    ), patch(
        "aird.handlers.transfer_ws_handlers.finalize_upload_to_disk",
        return_value=(True, 200, "Upload successful"),
    ) as mock_finalize:
        await handler._handle_upload_start(
            {
                "upload_dir": "",
                "filename": "test.txt",
                "total_size": len(data),
            }
        )
        await handler._handle_upload_binary(data)
        await handler._handle_upload_end()
        mock_finalize.assert_called_once()


@pytest.mark.asyncio
async def test_download_streams_file(tmp_path):
    handler = make_transfer_ws_handler()
    authenticate(handler, role="user")
    file_path = tmp_path / "dl.txt"
    file_path.write_bytes(b"abc123")

    async def fake_chunks(path, chunk_size=65536):
        yield b"abc123"

    with patch(
        "aird.handlers.transfer_ws_handlers.get_user_root", return_value=str(tmp_path)
    ), patch(
        "aird.handlers.transfer_ws_handlers.is_feature_enabled", return_value=True
    ), patch(
        "aird.handlers.transfer_ws_handlers._ws_check_access", return_value=False
    ), patch(
        "aird.handlers.transfer_ws_handlers.is_within_root", return_value=True
    ), patch(
        "aird.handlers.transfer_ws_handlers.MMapFileHandler.serve_file_chunk",
        side_effect=fake_chunks,
    ):
        await handler._handle_download({"path": "dl.txt"})
        await handler._download_task

    types = []
    for call in handler.write_message.await_args_list:
        arg = call[0][0]
        if isinstance(arg, bytes):
            types.append("binary")
        else:
            types.append(json.loads(arg)["type"])
    assert "download_start" in types
    assert "binary" in types
    assert "download_end" in types


def test_ws_helper_functions():
    from aird.app_context import AppContext
    from aird.handlers.transfer_ws_handlers import (
        _ws_db_conn,
        _ws_display_username,
        _ws_get_service,
        _ws_has_modify_privileges,
    )

    handler = MagicMock()
    handler.get_current_user.return_value = None
    assert _ws_has_modify_privileges(handler) is False
    assert _ws_display_username(handler) == "Guest"

    handler.get_current_user.return_value = {"username": "token_user", "role": "user"}
    assert _ws_has_modify_privileges(handler) is False

    handler.get_current_user.return_value = {"username": "alice", "role": "user"}
    assert _ws_has_modify_privileges(handler) is True
    assert _ws_display_username(handler) == "alice (User)"

    ctx = AppContext(db_conn=object(), services={"audit_service": object()})
    handler.settings = {"app_context": ctx}
    assert _ws_db_conn(handler) is ctx.db_conn
    assert _ws_get_service(handler, "audit_service") is not None

    handler.settings = {"db_conn": "db", "services": {"x": 1}}
    assert _ws_db_conn(handler) == "db"
    assert _ws_get_service(handler, "x") == 1
