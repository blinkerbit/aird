import pytest
from unittest.mock import patch, MagicMock, AsyncMock
from aird.handlers.api_handlers import SuperSearchWebSocketHandler
import json


class TestSuperSearchWebSocketHandler:
    def setup_method(self):
        self.mock_app = MagicMock()
        self.mock_request = MagicMock()
        self.mock_request.path = "/search"
        self.mock_app.settings = {"cookie_secret": "test_secret"}
        # Mock connection manager to avoid side effects
        self.mock_cm = MagicMock()
        self.mock_cm.add_connection.return_value = True
        SuperSearchWebSocketHandler.connection_manager = self.mock_cm

    @pytest.mark.asyncio
    async def test_auth_success_cookie(self):
        handler = SuperSearchWebSocketHandler(self.mock_app, self.mock_request)

        # Mock get_secure_cookie
        handler.get_secure_cookie = MagicMock(
            return_value=json.dumps({"username": "user"}).encode()
        )

        # Mock DB user check
        self.mock_app.settings["db_conn"] = MagicMock()
        with patch(
            "aird.handlers.base_handler.get_user_by_username",
            return_value={"username": "user"},
        ):

            user = handler.get_current_user()
            assert user["username"] == "user"

    @pytest.mark.asyncio
    async def test_auth_success_token(self):
        handler = SuperSearchWebSocketHandler(self.mock_app, self.mock_request)
        handler.get_secure_cookie = MagicMock(return_value=None)

        handler.request.headers = {"Authorization": "Bearer valid_token"}

        with patch("aird.config.ACCESS_TOKEN", "valid_token"):
            user = handler.get_current_user()
            assert user["username"] == "token_user"

    @pytest.mark.asyncio
    async def test_open_auth_failed(self):
        handler = SuperSearchWebSocketHandler(self.mock_app, self.mock_request)
        handler.get_current_user = MagicMock(return_value=None)

        with patch.object(handler, "close") as mock_close, patch.object(
            handler, "write_message"
        ) as mock_write:
            handler.open()
            mock_close.assert_called_with(code=1008, reason="Authentication required")
            mock_write.assert_called()  # Sends auth_required message

    @pytest.mark.asyncio
    async def test_search_execution(self):
        handler = SuperSearchWebSocketHandler(self.mock_app, self.mock_request)
        handler.get_current_user = MagicMock(return_value={"username": "user"})
        handler.write_message = MagicMock()

        # Mock os.walk and file operations
        with patch("os.walk") as mock_walk, patch("pathlib.Path") as mock_path, patch(
            "builtins.open", new_callable=MagicMock
        ) as mock_open:

            # Setup mock file system
            mock_walk.return_value = [("/root", [], ["file.txt"])]

            mock_file_path = MagicMock()
            mock_file_path.relative_to.return_value = "file.txt"
            mock_file_path.stat.return_value.st_size = 100
            mock_path.return_value.resolve.return_value = "/root"
            mock_path.return_value.__truediv__.return_value = mock_file_path

            # Mock file content
            mock_file = MagicMock()
            mock_file.__enter__.return_value = ["line with search_text\n"]
            mock_open.return_value = mock_file

            await handler.perform_search("*.txt", "search_text")

            # Verify matches sent
            # write_message called for search_start, match, and done
            assert handler.write_message.call_count >= 3

            # Check for match message
            calls = handler.write_message.call_args_list
            match_call = next((c for c in calls if "match" in c[0][0]), None)
            assert match_call is not None
            data = json.loads(match_call[0][0])
            assert data["type"] == "match"
            assert data["line_content"] == "line with search_text"

    @pytest.mark.asyncio
    async def test_on_message_start_search(self):
        handler = SuperSearchWebSocketHandler(self.mock_app, self.mock_request)
        handler.get_current_user = MagicMock(return_value={"username": "user"})
        handler.perform_search = AsyncMock()

        message = json.dumps({"pattern": "*.txt", "search_text": "foo"})
        await handler.on_message(message)

        handler.perform_search.assert_called_with("*.txt", "foo")

    @pytest.mark.asyncio
    async def test_search_no_matches(self):
        handler = SuperSearchWebSocketHandler(self.mock_app, self.mock_request)
        handler.get_current_user = MagicMock(return_value={"username": "user"})
        handler.write_message = MagicMock()

        with patch("os.walk", return_value=[]), patch("pathlib.Path"):

            await handler.perform_search("*.txt", "foo")

            calls = handler.write_message.call_args_list
            no_match_call = next((c for c in calls if "no_files" in c[0][0]), None)
            assert no_match_call is not None
            data = json.loads(no_match_call[0][0])
            assert data["type"] == "no_files"
            assert "files_searched" in data

    @pytest.mark.asyncio
    async def test_search_cancellation(self):
        handler = SuperSearchWebSocketHandler(self.mock_app, self.mock_request)
        handler.get_current_user = MagicMock(return_value={"username": "user"})
        handler.write_message = MagicMock()
        handler.stop_event.set()

        await handler.perform_search("*.txt", "foo")

        calls = handler.write_message.call_args_list
        cancelled_call = next((c for c in calls if "cancelled" in c[0][0]), None)
        assert cancelled_call is not None

    # ----------------------------------------------------------------
    # Scanning ticker & completion message tests
    # ----------------------------------------------------------------

    def _setup_handler_with_fs(self, walk_return, file_lines=None, file_size=100):
        """Helper: create a handler with mocked file system."""
        handler = SuperSearchWebSocketHandler(self.mock_app, self.mock_request)
        handler.get_current_user = MagicMock(return_value={"username": "user"})
        handler.write_message = MagicMock()

        mock_file_path = MagicMock()
        mock_file_path.stat.return_value.st_size = file_size

        def make_rel(root):
            """Return a mock that stringifies to the filename part."""
            result = MagicMock()
            result.__str__ = lambda s: mock_file_path._current_name
            return result

        mock_file_path.relative_to = make_rel

        root_mock = MagicMock()
        root_mock.resolve.return_value = root_mock

        def truediv(_, name):
            mock_file_path._current_name = name
            return mock_file_path

        root_mock.__truediv__ = truediv

        path_cls = MagicMock(return_value=root_mock)

        mock_open = MagicMock()
        mock_file = MagicMock()
        mock_file.__enter__ = MagicMock(return_value=iter(file_lines or []))
        mock_file.__exit__ = MagicMock(return_value=False)
        mock_open.return_value = mock_file

        return handler, walk_return, path_cls, mock_open

    def _get_messages(self, handler, msg_type=None):
        """Extract parsed JSON messages from write_message calls, optionally filtered by type."""
        msgs = []
        for c in handler.write_message.call_args_list:
            try:
                data = json.loads(c[0][0])
                if msg_type is None or data.get("type") == msg_type:
                    msgs.append(data)
            except (json.JSONDecodeError, IndexError):
                pass
        return msgs

    @pytest.mark.asyncio
    async def test_scanning_message_sent_for_each_matching_file(self):
        """Backend sends a 'scanning' message for every file that matches the glob."""
        handler, walk_return, path_cls, mock_open = self._setup_handler_with_fs(
            walk_return=[("/root", [], ["a.txt", "b.txt", "c.py"])],
            file_lines=["nothing here\n"],
        )

        with patch("os.walk", return_value=walk_return), patch(
            "pathlib.Path", path_cls
        ), patch("builtins.open", mock_open):
            await handler.perform_search("*.txt", "needle")

        scanning_msgs = self._get_messages(handler, "scanning")
        assert len(scanning_msgs) == 2
        scanned_paths = [m["file_path"] for m in scanning_msgs]
        assert "a.txt" in scanned_paths
        assert "b.txt" in scanned_paths

    @pytest.mark.asyncio
    async def test_scanning_message_contains_files_searched_counter(self):
        handler, walk_return, path_cls, mock_open = self._setup_handler_with_fs(
            walk_return=[("/root", [], ["x.txt", "y.txt"])],
            file_lines=["no match\n"],
        )

        with patch("os.walk", return_value=walk_return), patch(
            "pathlib.Path", path_cls
        ), patch("builtins.open", mock_open):
            await handler.perform_search("*.txt", "needle")

        scanning_msgs = self._get_messages(handler, "scanning")
        counters = [m["files_searched"] for m in scanning_msgs]
        assert counters == [1, 2]

    @pytest.mark.asyncio
    async def test_scanning_not_sent_for_non_matching_files(self):
        """Files that don't match the glob should not produce scanning messages."""
        handler, walk_return, path_cls, mock_open = self._setup_handler_with_fs(
            walk_return=[("/root", [], ["readme.md", "notes.md"])],
            file_lines=[],
        )

        with patch("os.walk", return_value=walk_return), patch(
            "pathlib.Path", path_cls
        ), patch("builtins.open", mock_open):
            await handler.perform_search("*.txt", "needle")

        scanning_msgs = self._get_messages(handler, "scanning")
        assert len(scanning_msgs) == 0

    @pytest.mark.asyncio
    async def test_search_complete_message_on_matches(self):
        handler, walk_return, path_cls, mock_open = self._setup_handler_with_fs(
            walk_return=[("/root", [], ["data.txt"])],
            file_lines=["hello needle world\n"],
        )

        with patch("os.walk", return_value=walk_return), patch(
            "pathlib.Path", path_cls
        ), patch("builtins.open", mock_open):
            await handler.perform_search("*.txt", "needle")

        complete_msgs = self._get_messages(handler, "search_complete")
        assert len(complete_msgs) == 1
        assert complete_msgs[0]["matches"] == 1
        assert complete_msgs[0]["files_searched"] == 1

    @pytest.mark.asyncio
    async def test_no_files_message_when_zero_matches(self):
        handler, walk_return, path_cls, mock_open = self._setup_handler_with_fs(
            walk_return=[("/root", [], ["data.txt"])],
            file_lines=["nothing relevant\n"],
        )

        with patch("os.walk", return_value=walk_return), patch(
            "pathlib.Path", path_cls
        ), patch("builtins.open", mock_open):
            await handler.perform_search("*.txt", "needle")

        no_files_msgs = self._get_messages(handler, "no_files")
        assert len(no_files_msgs) == 1
        assert no_files_msgs[0]["files_searched"] == 1
        assert "message" in no_files_msgs[0]

    @pytest.mark.asyncio
    async def test_message_order_scanning_before_match(self):
        """For a matching file, scanning message should appear before the match message."""
        handler, walk_return, path_cls, mock_open = self._setup_handler_with_fs(
            walk_return=[("/root", [], ["log.txt"])],
            file_lines=["found needle here\n"],
        )

        with patch("os.walk", return_value=walk_return), patch(
            "pathlib.Path", path_cls
        ), patch("builtins.open", mock_open):
            await handler.perform_search("*.txt", "needle")

        all_msgs = self._get_messages(handler)
        types = [m["type"] for m in all_msgs]
        scanning_idx = types.index("scanning")
        match_idx = types.index("match")
        assert scanning_idx < match_idx

    @pytest.mark.asyncio
    async def test_search_start_is_first_message(self):
        handler, walk_return, path_cls, mock_open = self._setup_handler_with_fs(
            walk_return=[("/root", [], ["f.txt"])],
            file_lines=["line\n"],
        )

        with patch("os.walk", return_value=walk_return), patch(
            "pathlib.Path", path_cls
        ), patch("builtins.open", mock_open):
            await handler.perform_search("*.txt", "needle")

        all_msgs = self._get_messages(handler)
        assert all_msgs[0]["type"] == "search_start"
        assert all_msgs[0]["pattern"] == "*.txt"
        assert all_msgs[0]["search_text"] == "needle"
