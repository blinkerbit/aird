import pytest
from unittest.mock import patch, MagicMock, AsyncMock
from aird.handlers.api_handlers import SuperSearchWebSocketHandler
import json
import asyncio

class TestSuperSearchWebSocketHandler:
    def setup_method(self):
        self.mock_app = MagicMock()
        self.mock_request = MagicMock()
        self.mock_request.path = "/search"
        self.mock_app.settings = {'cookie_secret': 'test_secret'}
        # Mock connection manager to avoid side effects
        self.mock_cm = MagicMock()
        self.mock_cm.add_connection.return_value = True
        SuperSearchWebSocketHandler.connection_manager = self.mock_cm

    @pytest.mark.asyncio
    async def test_auth_success_cookie(self):
        handler = SuperSearchWebSocketHandler(self.mock_app, self.mock_request)
        
        # Mock get_secure_cookie
        handler.get_secure_cookie = MagicMock(return_value=json.dumps({'username': 'user'}).encode())
        
        # Mock DB user check
        with patch('aird.handlers.api_handlers.constants_module.DB_CONN', MagicMock()), \
             patch('aird.db.get_user_by_username', return_value={'username': 'user'}):
            
            user = handler.get_current_user()
            assert user['username'] == 'user'

    @pytest.mark.asyncio
    async def test_auth_success_token(self):
        handler = SuperSearchWebSocketHandler(self.mock_app, self.mock_request)
        handler.get_secure_cookie = MagicMock(return_value=None)
        
        handler.request.headers = {'Authorization': 'Bearer valid_token'}
        
        with patch('aird.config.ACCESS_TOKEN', 'valid_token'):
            user = handler.get_current_user()
            assert user['username'] == 'token_user'

    @pytest.mark.asyncio
    async def test_open_auth_failed(self):
        handler = SuperSearchWebSocketHandler(self.mock_app, self.mock_request)
        handler.get_current_user = MagicMock(return_value=None)
        
        with patch.object(handler, 'close') as mock_close, \
             patch.object(handler, 'write_message') as mock_write:
            handler.open()
            mock_close.assert_called_with(code=1008, reason="Authentication required")
            mock_write.assert_called() # Sends auth_required message

    @pytest.mark.asyncio
    async def test_search_execution(self):
        handler = SuperSearchWebSocketHandler(self.mock_app, self.mock_request)
        handler.get_current_user = MagicMock(return_value={'username': 'user'})
        handler.write_message = MagicMock()
        
        # Mock os.walk and file operations
        with patch('os.walk') as mock_walk, \
             patch('pathlib.Path') as mock_path, \
             patch('builtins.open', new_callable=MagicMock) as mock_open:
            
            # Setup mock file system
            mock_walk.return_value = [('/root', [], ['file.txt'])]
            
            mock_file_path = MagicMock()
            mock_file_path.relative_to.return_value = 'file.txt'
            mock_file_path.stat.return_value.st_size = 100
            mock_path.return_value.resolve.return_value = '/root'
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
            match_call = next((c for c in calls if 'match' in c[0][0]), None)
            assert match_call is not None
            data = json.loads(match_call[0][0])
            assert data['type'] == 'match'
            assert data['line_content'] == 'line with search_text'

    @pytest.mark.asyncio
    async def test_on_message_start_search(self):
        handler = SuperSearchWebSocketHandler(self.mock_app, self.mock_request)
        handler.get_current_user = MagicMock(return_value={'username': 'user'})
        handler.perform_search = AsyncMock()
        
        message = json.dumps({'pattern': '*.txt', 'search_text': 'foo'})
        await handler.on_message(message)
        
        handler.perform_search.assert_called_with('*.txt', 'foo')

    @pytest.mark.asyncio
    async def test_search_no_matches(self):
        handler = SuperSearchWebSocketHandler(self.mock_app, self.mock_request)
        handler.get_current_user = MagicMock(return_value={'username': 'user'})
        handler.write_message = MagicMock()
        
        with patch('os.walk', return_value=[]), \
             patch('pathlib.Path'):
            
            await handler.perform_search("*.txt", "foo")
            
            # Check for no_matches message
            calls = handler.write_message.call_args_list
            no_match_call = next((c for c in calls if 'no_matches' in c[0][0]), None)
            assert no_match_call is not None

    @pytest.mark.asyncio
    async def test_search_cancellation(self):
        handler = SuperSearchWebSocketHandler(self.mock_app, self.mock_request)
        handler.get_current_user = MagicMock(return_value={'username': 'user'})
        handler.write_message = MagicMock()
        handler.stop_event.set()
        
        await handler.perform_search("*.txt", "foo")
        
        # Should send cancelled message
        calls = handler.write_message.call_args_list
        cancelled_call = next((c for c in calls if 'cancelled' in c[0][0]), None)
        assert cancelled_call is not None
