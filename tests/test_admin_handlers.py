"""
Unit tests for AdminHandler in aird.handlers.admin_handlers module.

These tests cover admin panel functionality including feature flags management,
WebSocket configuration, and authentication checks.
"""

import pytest
import json
from unittest.mock import patch, MagicMock, call

from tests.handler_helpers import authenticate, patch_db_conn

# Import with error handling for missing module
try:
    from aird.handlers.admin_handlers import (
        AdminHandler,
        WebSocketStatsHandler,
        AdminUsersHandler,
        UserCreateHandler,
        UserEditHandler,
        UserDeleteHandler,
        LDAPConfigHandler,
        LDAPConfigCreateHandler,
        LDAPConfigEditHandler,
        LDAPConfigDeleteHandler,
        LDAPSyncHandler,
    )
    from aird.handlers.base_handler import BaseHandler
    AIRD_AVAILABLE = True
except ImportError:
    AIRD_AVAILABLE = False


@pytest.mark.skipif(not AIRD_AVAILABLE, reason="aird.handlers.admin_handlers module not available")
class TestAdminHandler:
    """Test AdminHandler functionality"""
    
    def setup_method(self):
        """Setup test fixtures"""
        self.mock_app = MagicMock()
        self.mock_request = MagicMock()
        self.mock_app.settings = {
            'cookie_secret': 'test_secret_key_for_testing',
            'template_path': 'templates',
            'debug': False,
            'login_url': '/login',
            'ldap_server': None
        }
        self.mock_request.connection = MagicMock()
        self.mock_request.connection.context = MagicMock()
        self.mock_request.protocol = "http"
    
    def test_admin_handler_creation(self, mock_tornado_app, mock_tornado_request):
        """Test AdminHandler can be instantiated"""
        handler = AdminHandler(mock_tornado_app, mock_tornado_request)
        assert handler.application == mock_tornado_app
        assert handler.request == mock_tornado_request
    
    def test_get_not_admin_user_redirects(self, mock_tornado_app, mock_tornado_request):
        """Test AdminHandler GET redirects to login when user is not admin"""
        handler = AdminHandler(mock_tornado_app, mock_tornado_request)
        
        with patch.object(handler, 'get_current_user', return_value={'username': 'user', 'role': 'user'}), \
             patch.object(handler, 'is_admin_user', return_value=False), \
             patch.object(handler, 'redirect') as mock_redirect:
            
            handler.get()
            
            # Should redirect to admin login
            mock_redirect.assert_called_once_with("/admin/login")
    
    def test_get_admin_user_with_db_connection(self, mock_tornado_app, mock_tornado_request, mock_db_conn):
        """Test AdminHandler GET when admin user with DB connection"""
        handler = AdminHandler(mock_tornado_app, mock_tornado_request)
        
        with patch.object(handler, 'get_current_user', return_value={'username': 'admin', 'role': 'admin'}), \
             patch.object(handler, 'is_admin_user', return_value=True), \
             patch('aird.handlers.admin_handlers.FEATURE_FLAGS', {'file_upload': True, 'file_delete': False}), \
             patch('aird.db.load_feature_flags', return_value={'file_upload': True, 'file_delete': False}), \
             patch('aird.utils.util.get_current_websocket_config', return_value={'max_connections': 50}), \
             patch.object(handler, 'render') as mock_render:
            
            handler.get()
            
            # Should render admin template with features
            assert mock_render.called
            call_args = mock_render.call_args
            if call_args:
                args, kwargs = call_args
                assert args[0] == "admin.html"
                assert 'features' in kwargs
                assert 'websocket_config' in kwargs
                assert 'ldap_enabled' in kwargs
                assert kwargs['ldap_enabled'] is False
    
    def test_get_admin_user_without_db_connection(self, mock_tornado_app, mock_tornado_request):
        """Test AdminHandler GET when admin user without DB connection"""
        handler = AdminHandler(mock_tornado_app, mock_tornado_request)
        
        # When DB is None, the handler should fall back to in-memory FEATURE_FLAGS
        in_memory_flags = {'file_upload': True}
        
        with patch.object(handler, 'get_current_user', return_value={'username': 'admin', 'role': 'admin'}), \
             patch.object(handler, 'is_admin_user', return_value=True), \
             patch_db_conn(None), \
             patch('aird.handlers.admin_handlers.FEATURE_FLAGS', in_memory_flags), \
             patch('aird.utils.util.get_current_websocket_config', return_value={}), \
             patch.object(handler, 'render') as mock_render:
            
            handler.get()
            
            assert mock_render.called
            call_args = mock_render.call_args
            if call_args:
                args, kwargs = call_args
                assert args[0] == "admin.html"
                assert kwargs['features'] == in_memory_flags
    
    def test_get_admin_user_with_ldap_enabled(self, mock_tornado_app, mock_tornado_request):
        """Test AdminHandler GET when LDAP is enabled"""
        mock_tornado_app.settings['ldap_server'] = 'ldap://example.com'
        handler = AdminHandler(mock_tornado_app, mock_tornado_request)
        
        with patch.object(handler, 'get_current_user', return_value={'username': 'admin', 'role': 'admin'}), \
             patch.object(handler, 'is_admin_user', return_value=True), \
             patch_db_conn(None), \
             patch('aird.handlers.admin_handlers.FEATURE_FLAGS', {}), \
             patch('aird.utils.util.get_current_websocket_config', return_value={}), \
             patch.object(handler, 'render') as mock_render:
            
            handler.get()
            
            # Should render with ldap_enabled=True
            assert mock_render.called
            call_args = mock_render.call_args
            if call_args:
                args, kwargs = call_args
                assert kwargs['ldap_enabled'] is True
    
    def test_post_not_admin_user_returns_403(self, mock_tornado_app, mock_tornado_request):
        """Test AdminHandler POST returns 403 when user is not admin"""
        handler = AdminHandler(mock_tornado_app, mock_tornado_request)
        
        with patch.object(handler, 'get_current_user', return_value={'username': 'user', 'role': 'user'}), \
             patch.object(handler, 'is_admin_user', return_value=False), \
             patch.object(handler, 'set_status') as mock_set_status, \
             patch.object(handler, 'write') as mock_write:
            
            handler.post()
            
            # Should return 403 Forbidden
            mock_set_status.assert_called_once_with(403)
            assert mock_write.called
            call_args = mock_write.call_args
            if call_args:
                assert call_args[0][0] == "Access denied: You don't have permission to perform this action"
    
    def test_post_admin_user_updates_feature_flags(self, mock_tornado_app, mock_tornado_request, mock_db_conn):
        """Test AdminHandler POST updates feature flags"""
        handler = AdminHandler(mock_tornado_app, mock_tornado_request)
        
        # Mock request arguments
        handler.get_argument = MagicMock(side_effect=lambda key, default="off": {
            'file_upload': 'on',
            'file_delete': 'off',
            'file_rename': 'on',
            'file_download': 'off',
            'file_edit': 'on',
            'file_share': 'off',
            'super_search': 'on',
            'compression': 'off',
            'feature_flags_max_connections': '100',
            'feature_flags_idle_timeout': '600',
            'file_streaming_max_connections': '200',
            'file_streaming_idle_timeout': '300',
            'search_max_connections': '50',
            'search_idle_timeout': '180'
        }.get(key, default))
        
        # Create mutable dicts for FEATURE_FLAGS and WEBSOCKET_CONFIG
        feature_flags = {}
        websocket_config = {}
        
        with patch.object(handler, 'get_current_user', return_value={'username': 'admin', 'role': 'admin'}), \
             patch.object(handler, 'is_admin_user', return_value=True), \
             patch_db_conn(mock_db_conn), \
             patch('aird.handlers.admin_handlers.FEATURE_FLAGS', feature_flags), \
             patch('aird.handlers.admin_handlers.WEBSOCKET_CONFIG', websocket_config), \
             patch('aird.handlers.admin_handlers.save_feature_flags') as mock_save_flags, \
             patch('aird.handlers.admin_handlers.save_websocket_config') as mock_save_ws_config, \
             patch('aird.handlers.api_handlers.FeatureFlagSocketHandler.send_updates') as mock_send_updates, \
             patch.object(handler, 'redirect') as mock_redirect:
            
            handler.post()
            
            # Should save feature flags and WebSocket config
            mock_save_flags.assert_called_once()
            mock_save_ws_config.assert_called_once()
            mock_send_updates.assert_called_once()
            mock_redirect.assert_called_once_with("/admin")
    
    def test_post_updates_websocket_config_with_limits(self, mock_tornado_app, mock_tornado_request, mock_db_conn):
        """Test AdminHandler POST validates WebSocket config limits"""
        handler = AdminHandler(mock_tornado_app, mock_tornado_request)
        
        # Mock request arguments with extreme values
        handler.get_argument = MagicMock(side_effect=lambda key, default="off": {
            'file_upload': 'off',
            'file_delete': 'off',
            'file_rename': 'off',
            'file_download': 'off',
            'file_edit': 'off',
            'file_share': 'off',
            'super_search': 'off',
            'compression': 'off',
            'feature_flags_max_connections': '2000',  # Should be clamped to 1000
            'feature_flags_idle_timeout': '10000',  # Should be clamped to 7200
            'file_streaming_max_connections': '0',  # Should be clamped to 1
            'file_streaming_idle_timeout': '10',  # Should be clamped to 30
            'search_max_connections': '500',
            'search_idle_timeout': '100'
        }.get(key, default))
        
        # Create mutable dicts for FEATURE_FLAGS and WEBSOCKET_CONFIG
        feature_flags = {}
        websocket_config = {}
        
        with patch.object(handler, 'get_current_user', return_value={'username': 'admin', 'role': 'admin'}), \
             patch.object(handler, 'is_admin_user', return_value=True), \
             patch_db_conn(mock_db_conn), \
             patch('aird.handlers.admin_handlers.FEATURE_FLAGS', feature_flags), \
             patch('aird.handlers.admin_handlers.WEBSOCKET_CONFIG', websocket_config), \
             patch('aird.handlers.admin_handlers.save_feature_flags'), \
             patch('aird.handlers.admin_handlers.save_websocket_config') as mock_save_ws_config, \
             patch('aird.handlers.api_handlers.FeatureFlagSocketHandler.send_updates'), \
             patch.object(handler, 'redirect'):
            
            handler.post()
            
            # Check that WebSocket config was saved with clamped values
            mock_save_ws_config.assert_called_once()
            call_args = mock_save_ws_config.call_args
            if call_args:
                saved_config = call_args[0][1]
                assert saved_config['feature_flags_max_connections'] == 1000
                assert saved_config['feature_flags_idle_timeout'] == 7200
                assert saved_config['file_streaming_max_connections'] == 1
                assert saved_config['file_streaming_idle_timeout'] == 30
    
    def test_post_handles_invalid_websocket_config(self, mock_tornado_app, mock_tornado_request, mock_db_conn):
        """Test AdminHandler POST handles invalid WebSocket config gracefully"""
        handler = AdminHandler(mock_tornado_app, mock_tornado_request)
        
        # Mock request arguments with invalid values
        handler.get_argument = MagicMock(side_effect=lambda key, default="off": {
            'file_upload': 'off',
            'file_delete': 'off',
            'file_rename': 'off',
            'file_download': 'off',
            'file_edit': 'off',
            'file_share': 'off',
            'super_search': 'off',
            'compression': 'off',
            'feature_flags_max_connections': 'invalid',  # Invalid value
            'feature_flags_idle_timeout': '600',
            'file_streaming_max_connections': '200',
            'file_streaming_idle_timeout': '300',
            'search_max_connections': '50',
            'search_idle_timeout': '180'
        }.get(key, default))
        
        with patch.object(handler, 'get_current_user', return_value={'username': 'admin', 'role': 'admin'}), \
             patch.object(handler, 'is_admin_user', return_value=True), \
             patch('aird.handlers.admin_handlers.FEATURE_FLAGS', {}), \
             patch('aird.handlers.admin_handlers.WEBSOCKET_CONFIG', {'feature_flags_max_connections': 50}), \
             patch('aird.handlers.admin_handlers.save_feature_flags'), \
             patch('aird.handlers.admin_handlers.save_websocket_config'), \
             patch('aird.handlers.api_handlers.FeatureFlagSocketHandler.send_updates'), \
             patch.object(handler, 'redirect') as mock_redirect:
            
            # Should not raise exception, should use current values
            handler.post()
            
            # Should still redirect
            assert mock_redirect.called
            call_args = mock_redirect.call_args
            if call_args:
                assert call_args[0][0] == "/admin"
    
    def test_post_handles_db_error_gracefully(self, mock_tornado_app, mock_tornado_request, mock_db_conn):
        """Test AdminHandler POST handles database errors gracefully"""
        handler = AdminHandler(mock_tornado_app, mock_tornado_request)
        
        handler.get_argument = MagicMock(side_effect=lambda key, default="off": {
            'file_upload': 'on',
            'file_delete': 'off',
            'file_rename': 'off',
            'file_download': 'off',
            'file_edit': 'off',
            'file_share': 'off',
            'super_search': 'off',
            'compression': 'off',
            'feature_flags_max_connections': '50',
            'feature_flags_idle_timeout': '600',
            'file_streaming_max_connections': '200',
            'file_streaming_idle_timeout': '300',
            'search_max_connections': '50',
            'search_idle_timeout': '180'
        }.get(key, default))
        
        with patch.object(handler, 'get_current_user', return_value={'username': 'admin', 'role': 'admin'}), \
             patch.object(handler, 'is_admin_user', return_value=True), \
             patch_db_conn(mock_db_conn), \
             patch('aird.handlers.admin_handlers.FEATURE_FLAGS', {}), \
             patch('aird.handlers.admin_handlers.WEBSOCKET_CONFIG', {}), \
             patch('aird.handlers.admin_handlers.save_feature_flags', side_effect=Exception("DB Error")), \
             patch('aird.handlers.admin_handlers.save_websocket_config', side_effect=Exception("DB Error")), \
             patch('aird.handlers.api_handlers.FeatureFlagSocketHandler.send_updates'), \
             patch.object(handler, 'redirect') as mock_redirect:
            
            # Should not raise exception, should still redirect
            handler.post()
            
            assert mock_redirect.called
            call_args = mock_redirect.call_args
            if call_args:
                assert call_args[0][0] == "/admin"


@pytest.mark.skipif(not AIRD_AVAILABLE, reason="aird.handlers.admin_handlers module not available")
class TestWebSocketStatsHandler:
    """Test WebSocketStatsHandler functionality"""
    
    def setup_method(self):
        """Setup test fixtures"""
        self.mock_app = MagicMock()
        self.mock_request = MagicMock()
        self.mock_app.settings = {'login_url': '/login'}
        self.mock_request.connection = MagicMock()
        self.mock_request.connection.context = MagicMock()
    
    def test_get_not_admin_user_returns_403(self, mock_tornado_app, mock_tornado_request):
        """Test WebSocketStatsHandler GET returns 403 when user is not admin"""
        handler = WebSocketStatsHandler(mock_tornado_app, mock_tornado_request)
        
        with patch.object(handler, 'get_current_user', return_value={'username': 'user', 'role': 'user'}), \
             patch.object(handler, 'is_admin_user', return_value=False), \
             patch.object(handler, 'set_status') as mock_set_status, \
             patch.object(handler, 'write') as mock_write:
            
            handler.get()
            
            # Should return 403 Forbidden
            mock_set_status.assert_called_once_with(403)
            assert mock_write.called
            call_args = mock_write.call_args
            if call_args:
                assert call_args[0][0] == "Forbidden"
    
    def test_get_admin_user_returns_stats(self, mock_tornado_app, mock_tornado_request):
        """Test WebSocketStatsHandler GET returns WebSocket statistics"""
        handler = WebSocketStatsHandler(mock_tornado_app, mock_tornado_request)
        
        # Mock connection managers
        mock_feature_manager = MagicMock()
        mock_feature_manager.get_stats.return_value = {
            'total_connections': 5,
            'max_connections': 50
        }
        
        mock_stream_manager = MagicMock()
        mock_stream_manager.get_stats.return_value = {
            'total_connections': 8,
            'max_connections': 200
        }
        
        mock_search_manager = MagicMock()
        mock_search_manager.get_stats.return_value = {
            'total_connections': 3,
            'max_connections': 100
        }
        
        with patch.object(handler, 'get_current_user', return_value={'username': 'admin', 'role': 'admin'}), \
             patch.object(handler, 'is_admin_user', return_value=True), \
             patch('aird.handlers.api_handlers.FeatureFlagSocketHandler') as mock_feature_handler, \
             patch('aird.handlers.api_handlers.FileStreamHandler') as mock_stream_handler, \
             patch('aird.handlers.api_handlers.SuperSearchWebSocketHandler') as mock_search_handler, \
             patch.object(handler, 'set_header') as mock_set_header, \
             patch.object(handler, 'write') as mock_write:
            
            mock_feature_handler.connection_manager = mock_feature_manager
            mock_stream_handler.connection_manager = mock_stream_manager
            mock_search_handler.connection_manager = mock_search_manager
            
            handler.get()
            
            # Should set JSON content type
            mock_set_header.assert_called_once_with('Content-Type', 'application/json')
            
            # Should write JSON stats
            assert mock_write.called
            call_args = mock_write.call_args
            if call_args:
                written_data = call_args[0][0]
                stats = json.loads(written_data)
                
                assert 'feature_flags' in stats
                assert 'file_streaming' in stats
                assert 'super_search' in stats
                assert 'timestamp' in stats
                assert stats['feature_flags']['total_connections'] == 5
                assert stats['file_streaming']['total_connections'] == 8
                assert stats['super_search']['total_connections'] == 3


@pytest.mark.skipif(not AIRD_AVAILABLE, reason="aird.handlers.admin_handlers module not available")
class TestAdminUsersHandler:
    def test_get_redirects_when_not_admin(self, mock_tornado_app, mock_tornado_request):
        handler = AdminUsersHandler(mock_tornado_app, mock_tornado_request)
        authenticate(handler)
        with patch.object(handler, 'is_admin_user', return_value=False), \
             patch.object(handler, 'redirect') as mock_redirect:
            handler.get()
            mock_redirect.assert_called_once_with("/admin/login")

    def test_get_renders_users_when_db_available(self, mock_tornado_app, mock_tornado_request, mock_db_conn):
        handler = AdminUsersHandler(mock_tornado_app, mock_tornado_request)
        authenticate(handler)
        sample_users = [{'id': 1, 'username': 'alice'}]
        with patch.object(handler, 'is_admin_user', return_value=True), \
             patch_db_conn(mock_db_conn), \
             patch('aird.handlers.admin_handlers.get_all_users', return_value=sample_users), \
             patch.object(handler, 'render') as mock_render:
            handler.get()
            mock_render.assert_called_once()
            args, kwargs = mock_render.call_args
            assert args[0] == "admin_users.html"
            assert kwargs['users'] == sample_users

    def test_get_renders_empty_when_db_missing(self, mock_tornado_app, mock_tornado_request):
        handler = AdminUsersHandler(mock_tornado_app, mock_tornado_request)
        authenticate(handler)
        with patch.object(handler, 'is_admin_user', return_value=True), \
             patch_db_conn(None), \
             patch.object(handler, 'render') as mock_render:
            handler.get()
            mock_render.assert_called_once_with("admin_users.html", users=[])


@pytest.mark.skipif(not AIRD_AVAILABLE, reason="aird.handlers.admin_handlers module not available")
class TestUserCreateHandler:
    def test_get_not_admin_returns_403(self, mock_tornado_app, mock_tornado_request):
        handler = UserCreateHandler(mock_tornado_app, mock_tornado_request)
        authenticate(handler, role='user')
        with patch.object(handler, 'is_admin_user', return_value=False), \
             patch.object(handler, 'set_status') as mock_status, \
             patch.object(handler, 'write') as mock_write:
            handler.get()
            mock_status.assert_called_once_with(403)
            assert mock_write.called

    def test_post_creates_user_successfully(self, mock_tornado_app, mock_tornado_request, mock_db_conn):
        handler = UserCreateHandler(mock_tornado_app, mock_tornado_request)
        authenticate(handler)
        handler.request.method = "POST"
        handler.get_argument = MagicMock(side_effect=lambda key, default=None: {
            'username': 'newuser',
            'password': 'securepass',
            'role': 'admin'
        }.get(key, default))
        with patch.object(handler, 'is_admin_user', return_value=True), \
             patch_db_conn(mock_db_conn), \
             patch('aird.handlers.admin_handlers.create_user') as mock_create_user, \
             patch.object(handler, 'redirect') as mock_redirect:
            handler.post()
            mock_create_user.assert_called_once_with(mock_db_conn, 'newuser', 'securepass', 'admin')
            mock_redirect.assert_called_once_with("/admin/users")

    def test_get_admin_renders_form(self, mock_tornado_app, mock_tornado_request):
        handler = UserCreateHandler(mock_tornado_app, mock_tornado_request)
        authenticate(handler)
        with patch.object(handler, 'is_admin_user', return_value=True), \
             patch.object(handler, 'render') as mock_render:
            handler.get()
            mock_render.assert_called_once_with("user_create.html", error=None)

    def test_post_renders_error_when_db_missing(self, mock_tornado_app, mock_tornado_request):
        handler = UserCreateHandler(mock_tornado_app, mock_tornado_request)
        authenticate(handler)
        handler.request.method = "POST"
        with patch.object(handler, 'is_admin_user', return_value=True), \
             patch_db_conn(None), \
             patch.object(handler, 'render') as mock_render:
            handler.post()
            mock_render.assert_called_once()
            args, kwargs = mock_render.call_args
            assert args[0] == "user_create.html"
            assert kwargs['error'] == "Database not available"

    def test_post_missing_username(self, mock_tornado_app, mock_tornado_request, mock_db_conn):
        handler = UserCreateHandler(mock_tornado_app, mock_tornado_request)
        authenticate(handler)
        handler.request.method = "POST"
        handler.get_argument = MagicMock(side_effect=lambda key, default=None: {
            'username': '',
            'password': 'securepass',
            'role': 'user'
        }.get(key, default))
        with patch.object(handler, 'is_admin_user', return_value=True), \
             patch_db_conn(mock_db_conn), \
             patch.object(handler, 'render') as mock_render:
            handler.post()
            mock_render.assert_called_once()
            args, kwargs = mock_render.call_args
            assert kwargs['error'] == "Username and password are required"

    def test_post_short_password_rejected(self, mock_tornado_app, mock_tornado_request, mock_db_conn):
        handler = UserCreateHandler(mock_tornado_app, mock_tornado_request)
        authenticate(handler)
        handler.request.method = "POST"
        handler.get_argument = MagicMock(side_effect=lambda key, default=None: {
            'username': 'shorty',
            'password': '123',
            'role': 'user'
        }.get(key, default))
        with patch.object(handler, 'is_admin_user', return_value=True), \
             patch_db_conn(mock_db_conn), \
             patch.object(handler, 'render') as mock_render:
            handler.post()
            mock_render.assert_called_once()
            assert mock_render.call_args.kwargs['error'] == "Password must be at least 6 characters"

    def test_post_invalid_role(self, mock_tornado_app, mock_tornado_request, mock_db_conn):
        handler = UserCreateHandler(mock_tornado_app, mock_tornado_request)
        authenticate(handler)
        handler.request.method = "POST"
        handler.get_argument = MagicMock(side_effect=lambda key, default=None: {
            'username': 'newuser',
            'password': 'securepass',
            'role': 'super'
        }.get(key, default))
        with patch.object(handler, 'is_admin_user', return_value=True), \
             patch_db_conn(mock_db_conn), \
             patch.object(handler, 'render') as mock_render:
            handler.post()
            mock_render.assert_called_once()
            assert mock_render.call_args.kwargs['error'] == "Invalid role"

    def test_post_invalid_username_characters(self, mock_tornado_app, mock_tornado_request, mock_db_conn):
        handler = UserCreateHandler(mock_tornado_app, mock_tornado_request)
        authenticate(handler)
        handler.request.method = "POST"
        handler.get_argument = MagicMock(side_effect=lambda key, default=None: {
            'username': 'bad!name',
            'password': 'securepass',
            'role': 'user'
        }.get(key, default))
        with patch.object(handler, 'is_admin_user', return_value=True), \
             patch_db_conn(mock_db_conn), \
             patch.object(handler, 'render') as mock_render:
            handler.post()
            mock_render.assert_called_once()
            assert "Username can only contain" in mock_render.call_args.kwargs['error']

    def test_post_handles_create_user_value_error(self, mock_tornado_app, mock_tornado_request, mock_db_conn):
        handler = UserCreateHandler(mock_tornado_app, mock_tornado_request)
        authenticate(handler)
        handler.request.method = "POST"
        handler.get_argument = MagicMock(side_effect=lambda key, default=None: {
            'username': 'newuser',
            'password': 'securepass',
            'role': 'user'
        }.get(key, default))
        with patch.object(handler, 'is_admin_user', return_value=True), \
             patch_db_conn(mock_db_conn), \
             patch('aird.handlers.admin_handlers.create_user', side_effect=ValueError("duplicate")) as mock_create, \
             patch.object(handler, 'render') as mock_render:
            handler.post()
            mock_create.assert_called_once()
            assert mock_render.call_args.kwargs['error'] == "duplicate"

    def test_post_handles_create_user_exception(self, mock_tornado_app, mock_tornado_request, mock_db_conn):
        handler = UserCreateHandler(mock_tornado_app, mock_tornado_request)
        authenticate(handler)
        handler.request.method = "POST"
        handler.get_argument = MagicMock(side_effect=lambda key, default=None: {
            'username': 'newuser',
            'password': 'securepass',
            'role': 'user'
        }.get(key, default))
        with patch.object(handler, 'is_admin_user', return_value=True), \
             patch_db_conn(mock_db_conn), \
             patch('aird.handlers.admin_handlers.create_user', side_effect=Exception("boom")), \
             patch.object(handler, 'render') as mock_render:
            handler.post()
            assert mock_render.call_args.kwargs['error'] == "Failed to create user"


@pytest.mark.skipif(not AIRD_AVAILABLE, reason="aird.handlers.admin_handlers module not available")
class TestUserEditHandler:
    def test_get_returns_404_when_user_missing(self, mock_tornado_app, mock_tornado_request, mock_db_conn):
        handler = UserEditHandler(mock_tornado_app, mock_tornado_request)
        authenticate(handler)
        with patch.object(handler, 'is_admin_user', return_value=True), \
             patch_db_conn(mock_db_conn), \
             patch('aird.handlers.admin_handlers.get_all_users', return_value=[]), \
             patch.object(handler, 'set_status') as mock_status, \
             patch.object(handler, 'write') as mock_write:
            handler.get("1")
            mock_status.assert_called_once_with(404)
            assert mock_write.called

    def test_post_updates_user(self, mock_tornado_app, mock_tornado_request, mock_db_conn):
        handler = UserEditHandler(mock_tornado_app, mock_tornado_request)
        authenticate(handler)
        handler.request.method = "POST"
        handler.get_argument = MagicMock(side_effect=lambda key, default=None: {
            'username': 'updated',
            'password': '',
            'role': 'user',
            'active': 'on'
        }.get(key, default))
        existing_user = {'id': 1, 'username': 'old', 'role': 'user'}
        with patch.object(handler, 'is_admin_user', return_value=True), \
             patch_db_conn(mock_db_conn), \
             patch('aird.handlers.admin_handlers.get_all_users', return_value=[existing_user]), \
             patch('aird.handlers.admin_handlers.update_user', return_value=True) as mock_update_user, \
             patch.object(handler, 'redirect') as mock_redirect:
            handler.post("1")
            mock_update_user.assert_called_once()
            mock_redirect.assert_called_once_with("/admin/users")

    def test_get_not_admin_denied(self, mock_tornado_app, mock_tornado_request):
        handler = UserEditHandler(mock_tornado_app, mock_tornado_request)
        authenticate(handler, role='user')
        with patch.object(handler, 'is_admin_user', return_value=False), \
             patch.object(handler, 'set_status') as mock_status, \
             patch.object(handler, 'write') as mock_write:
            handler.get("1")
            mock_status.assert_called_once_with(403)
            assert mock_write.called

    def test_get_returns_500_when_db_missing(self, mock_tornado_app, mock_tornado_request):
        handler = UserEditHandler(mock_tornado_app, mock_tornado_request)
        authenticate(handler)
        with patch.object(handler, 'is_admin_user', return_value=True), \
             patch_db_conn(None), \
             patch.object(handler, 'set_status') as mock_status, \
             patch.object(handler, 'write') as mock_write:
            handler.get("1")
            mock_status.assert_called_once_with(500)
            assert "Database connection error" in mock_write.call_args[0][0]

    def test_get_invalid_user_id_returns_400(self, mock_tornado_app, mock_tornado_request, mock_db_conn):
        handler = UserEditHandler(mock_tornado_app, mock_tornado_request)
        authenticate(handler)
        with patch.object(handler, 'is_admin_user', return_value=True), \
             patch_db_conn(mock_db_conn), \
             patch.object(handler, 'set_status') as mock_status, \
             patch.object(handler, 'write') as mock_write:
            handler.get("abc")
            mock_status.assert_called_once_with(400)
            assert "valid user ID" in mock_write.call_args[0][0]

    def test_post_not_admin_denied(self, mock_tornado_app, mock_tornado_request):
        handler = UserEditHandler(mock_tornado_app, mock_tornado_request)
        authenticate(handler, role='user')
        handler.request.method = "POST"
        with patch.object(handler, 'is_admin_user', return_value=False), \
             patch.object(handler, 'set_status') as mock_status, \
             patch.object(handler, 'write') as mock_write:
            handler.post("1")
            mock_status.assert_called_once_with(403)
            assert mock_write.called

    def test_post_returns_500_when_db_missing(self, mock_tornado_app, mock_tornado_request):
        handler = UserEditHandler(mock_tornado_app, mock_tornado_request)
        authenticate(handler)
        handler.request.method = "POST"
        with patch.object(handler, 'is_admin_user', return_value=True), \
             patch_db_conn(None), \
             patch.object(handler, 'set_status') as mock_status, \
             patch.object(handler, 'write') as mock_write:
            handler.post("1")
            mock_status.assert_called_once_with(500)
            assert "Database connection error" in mock_write.call_args[0][0]

    def test_post_user_not_found_returns_404(self, mock_tornado_app, mock_tornado_request, mock_db_conn):
        handler = UserEditHandler(mock_tornado_app, mock_tornado_request)
        authenticate(handler)
        handler.request.method = "POST"
        with patch.object(handler, 'is_admin_user', return_value=True), \
             patch_db_conn(mock_db_conn), \
             patch('aird.handlers.admin_handlers.get_all_users', return_value=[]), \
             patch.object(handler, 'set_status') as mock_status, \
             patch.object(handler, 'write') as mock_write:
            handler.post("1")
            mock_status.assert_called_once_with(404)
            assert "User not found" in mock_write.call_args[0][0]

    def test_post_invalid_username_renders_error(self, mock_tornado_app, mock_tornado_request, mock_db_conn):
        handler = UserEditHandler(mock_tornado_app, mock_tornado_request)
        authenticate(handler)
        handler.request.method = "POST"
        handler.get_argument = MagicMock(side_effect=lambda key, default=None: {
            'username': '',
            'password': '',
            'role': 'user',
            'active': 'on'
        }.get(key, default))
        existing_user = {'id': 1, 'username': 'old', 'role': 'user'}
        with patch.object(handler, 'is_admin_user', return_value=True), \
             patch_db_conn(mock_db_conn), \
             patch('aird.handlers.admin_handlers.get_all_users', return_value=[existing_user]), \
             patch.object(handler, 'render') as mock_render:
            handler.post("1")
            mock_render.assert_called_once()
            assert mock_render.call_args.kwargs['error'] == "Username is required"

    def test_post_short_password_renders_error(self, mock_tornado_app, mock_tornado_request, mock_db_conn):
        handler = UserEditHandler(mock_tornado_app, mock_tornado_request)
        authenticate(handler)
        handler.request.method = "POST"
        handler.get_argument = MagicMock(side_effect=lambda key, default=None: {
            'username': 'user',
            'password': '123',
            'role': 'user',
            'active': 'on'
        }.get(key, default))
        existing_user = {'id': 1, 'username': 'old', 'role': 'user'}
        with patch.object(handler, 'is_admin_user', return_value=True), \
             patch_db_conn(mock_db_conn), \
             patch('aird.handlers.admin_handlers.get_all_users', return_value=[existing_user]), \
             patch.object(handler, 'render') as mock_render:
            handler.post("1")
            assert mock_render.call_args.kwargs['error'] == "Password must be at least 6 characters"

    def test_post_invalid_role_renders_error(self, mock_tornado_app, mock_tornado_request, mock_db_conn):
        handler = UserEditHandler(mock_tornado_app, mock_tornado_request)
        authenticate(handler)
        handler.request.method = "POST"
        handler.get_argument = MagicMock(side_effect=lambda key, default=None: {
            'username': 'user',
            'password': '',
            'role': 'guest',
            'active': 'on'
        }.get(key, default))
        existing_user = {'id': 1, 'username': 'old', 'role': 'user'}
        with patch.object(handler, 'is_admin_user', return_value=True), \
             patch_db_conn(mock_db_conn), \
             patch('aird.handlers.admin_handlers.get_all_users', return_value=[existing_user]), \
             patch.object(handler, 'render') as mock_render:
            handler.post("1")
            assert mock_render.call_args.kwargs['error'] == "Invalid role"

    def test_post_invalid_username_characters_renders_error(self, mock_tornado_app, mock_tornado_request, mock_db_conn):
        handler = UserEditHandler(mock_tornado_app, mock_tornado_request)
        authenticate(handler)
        handler.request.method = "POST"
        handler.get_argument = MagicMock(side_effect=lambda key, default=None: {
            'username': 'bad!name',
            'password': '',
            'role': 'user',
            'active': 'on'
        }.get(key, default))
        existing_user = {'id': 1, 'username': 'old', 'role': 'user'}
        with patch.object(handler, 'is_admin_user', return_value=True), \
             patch_db_conn(mock_db_conn), \
             patch('aird.handlers.admin_handlers.get_all_users', return_value=[existing_user]), \
             patch.object(handler, 'render') as mock_render:
            handler.post("1")
            assert "Username can only contain" in mock_render.call_args.kwargs['error']

    def test_post_password_change_blocked_when_ldap_enabled(self, mock_tornado_app, mock_tornado_request, mock_db_conn):
        handler = UserEditHandler(mock_tornado_app, mock_tornado_request)
        authenticate(handler)
        handler.settings['ldap_server'] = 'ldap://example.com'
        handler.request.method = "POST"
        handler.get_argument = MagicMock(side_effect=lambda key, default=None: {
            'username': 'user',
            'password': 'newpass',
            'role': 'user',
            'active': 'on'
        }.get(key, default))
        existing_user = {'id': 1, 'username': 'old', 'role': 'user'}
        with patch.object(handler, 'is_admin_user', return_value=True), \
             patch_db_conn(mock_db_conn), \
             patch('aird.handlers.admin_handlers.get_all_users', return_value=[existing_user]), \
             patch.object(handler, 'render') as mock_render:
            handler.post("1")
            assert "Password changes are not allowed" in mock_render.call_args.kwargs['error']

    def test_post_update_user_failure_renders_error(self, mock_tornado_app, mock_tornado_request, mock_db_conn):
        handler = UserEditHandler(mock_tornado_app, mock_tornado_request)
        authenticate(handler)
        handler.request.method = "POST"
        handler.get_argument = MagicMock(side_effect=lambda key, default=None: {
            'username': 'user',
            'password': '',
            'role': 'user',
            'active': 'on'
        }.get(key, default))
        existing_user = {'id': 1, 'username': 'old', 'role': 'user'}
        with patch.object(handler, 'is_admin_user', return_value=True), \
             patch_db_conn(mock_db_conn), \
             patch('aird.handlers.admin_handlers.get_all_users', return_value=[existing_user]), \
             patch('aird.handlers.admin_handlers.update_user', return_value=False), \
             patch.object(handler, 'render') as mock_render:
            handler.post("1")
            assert mock_render.call_args.kwargs['error'] == "Failed to update user"

    def test_post_invalid_user_id_returns_400(self, mock_tornado_app, mock_tornado_request, mock_db_conn):
        handler = UserEditHandler(mock_tornado_app, mock_tornado_request)
        authenticate(handler)
        handler.request.method = "POST"
        with patch.object(handler, 'is_admin_user', return_value=True), \
             patch_db_conn(mock_db_conn), \
             patch.object(handler, 'set_status') as mock_status, \
             patch.object(handler, 'write') as mock_write:
            handler.post("abc")
            mock_status.assert_called_once_with(400)
            assert "valid user ID" in mock_write.call_args[0][0]

    def test_post_handles_exception(self, mock_tornado_app, mock_tornado_request, mock_db_conn):
        handler = UserEditHandler(mock_tornado_app, mock_tornado_request)
        authenticate(handler)
        handler.request.method = "POST"
        handler.get_argument = MagicMock(side_effect=lambda key, default=None: {
            'username': 'user',
            'password': '',
            'role': 'user',
            'active': 'on'
        }.get(key, default))
        existing_user = {'id': 1, 'username': 'old', 'role': 'user'}
        with patch.object(handler, 'is_admin_user', return_value=True), \
             patch_db_conn(mock_db_conn), \
             patch('aird.handlers.admin_handlers.get_all_users', return_value=[existing_user]), \
             patch('aird.handlers.admin_handlers.update_user', side_effect=Exception("boom")), \
             patch.object(handler, 'render') as mock_render:
            handler.post("1")
            assert "Error updating user" in mock_render.call_args.kwargs['error']


@pytest.mark.skipif(not AIRD_AVAILABLE, reason="aird.handlers.admin_handlers module not available")
class TestUserDeleteHandler:
    def test_post_invalid_id_returns_400(self, mock_tornado_app, mock_tornado_request, mock_db_conn):
        handler = UserDeleteHandler(mock_tornado_app, mock_tornado_request)
        authenticate(handler)
        handler.get_argument = MagicMock(return_value="0")
        with patch.object(handler, 'is_admin_user', return_value=True), \
             patch_db_conn(mock_db_conn), \
             patch.object(handler, 'set_status') as mock_status, \
             patch.object(handler, 'write') as mock_write:
            handler.post()
            mock_status.assert_called_once_with(400)
            assert mock_write.called

    def test_post_deletes_user(self, mock_tornado_app, mock_tornado_request, mock_db_conn):
        handler = UserDeleteHandler(mock_tornado_app, mock_tornado_request)
        authenticate(handler)
        handler.get_argument = MagicMock(return_value="5")
        with patch.object(handler, 'is_admin_user', return_value=True), \
             patch_db_conn(mock_db_conn), \
             patch('aird.handlers.admin_handlers.delete_user', return_value=True) as mock_delete_user, \
             patch.object(handler, 'redirect') as mock_redirect:
            handler.post()
            mock_delete_user.assert_called_once_with(mock_db_conn, 5)
            mock_redirect.assert_called_once_with("/admin/users")

    def test_post_not_admin_denied(self, mock_tornado_app, mock_tornado_request):
        handler = UserDeleteHandler(mock_tornado_app, mock_tornado_request)
        authenticate(handler, role='user')
        handler.get_argument = MagicMock(return_value="1")
        with patch.object(handler, 'is_admin_user', return_value=False), \
             patch.object(handler, 'set_status') as mock_status, \
             patch.object(handler, 'write') as mock_write:
            handler.post()
            mock_status.assert_called_once_with(403)
            assert mock_write.called

    def test_post_returns_500_when_db_missing(self, mock_tornado_app, mock_tornado_request):
        handler = UserDeleteHandler(mock_tornado_app, mock_tornado_request)
        authenticate(handler)
        handler.get_argument = MagicMock(return_value="1")
        with patch.object(handler, 'is_admin_user', return_value=True), \
             patch_db_conn(None), \
             patch.object(handler, 'set_status') as mock_status, \
             patch.object(handler, 'write') as mock_write:
            handler.post()
            mock_status.assert_called_once_with(500)
            assert "Database connection error" in mock_write.call_args[0][0]

    def test_post_delete_user_returns_false(self, mock_tornado_app, mock_tornado_request, mock_db_conn):
        handler = UserDeleteHandler(mock_tornado_app, mock_tornado_request)
        authenticate(handler)
        handler.get_argument = MagicMock(return_value="3")
        with patch.object(handler, 'is_admin_user', return_value=True), \
             patch_db_conn(mock_db_conn), \
             patch('aird.handlers.admin_handlers.delete_user', return_value=False), \
             patch.object(handler, 'set_status') as mock_status, \
             patch.object(handler, 'write') as mock_write:
            handler.post()
            mock_status.assert_called_once_with(404)
            assert "User not found" in mock_write.call_args[0][0]

    def test_post_invalid_user_id_value_error(self, mock_tornado_app, mock_tornado_request, mock_db_conn):
        handler = UserDeleteHandler(mock_tornado_app, mock_tornado_request)
        authenticate(handler)
        handler.get_argument = MagicMock(return_value="abc")
        with patch.object(handler, 'is_admin_user', return_value=True), \
             patch_db_conn(mock_db_conn), \
             patch.object(handler, 'set_status') as mock_status, \
             patch.object(handler, 'write') as mock_write:
            handler.post()
            mock_status.assert_called_once_with(400)
            assert "valid user ID" in mock_write.call_args[0][0]


@pytest.mark.skipif(not AIRD_AVAILABLE, reason="aird.handlers.admin_handlers module not available")
class TestLDAPConfigHandler:
    def test_get_redirects_non_admin(self, mock_tornado_app, mock_tornado_request):
        handler = LDAPConfigHandler(mock_tornado_app, mock_tornado_request)
        authenticate(handler, role='user')
        with patch.object(handler, 'is_admin_user', return_value=False), \
             patch.object(handler, 'redirect') as mock_redirect:
            handler.get()
            mock_redirect.assert_called_once_with("/admin/login")

    def test_get_renders_configs(self, mock_tornado_app, mock_tornado_request, mock_db_conn):
        handler = LDAPConfigHandler(mock_tornado_app, mock_tornado_request)
        authenticate(handler)
        configs = [{'id': 1, 'name': 'Default'}]
        logs = [{'id': 1, 'status': 'ok'}]
        with patch.object(handler, 'is_admin_user', return_value=True), \
             patch_db_conn(mock_db_conn), \
             patch('aird.handlers.admin_handlers.get_all_ldap_configs', return_value=configs), \
             patch('aird.handlers.admin_handlers.get_ldap_sync_logs', return_value=logs), \
             patch.object(handler, 'render') as mock_render:
            handler.get()
            mock_render.assert_called_once_with("admin_ldap.html", configs=configs, sync_logs=logs)

    def test_get_handles_missing_db(self, mock_tornado_app, mock_tornado_request):
        handler = LDAPConfigHandler(mock_tornado_app, mock_tornado_request)
        authenticate(handler)
        with patch.object(handler, 'is_admin_user', return_value=True), \
             patch_db_conn(None), \
             patch.object(handler, 'render') as mock_render:
            handler.get()
            mock_render.assert_called_once_with("admin_ldap.html", configs=[], sync_logs=[])


@pytest.mark.skipif(not AIRD_AVAILABLE, reason="aird.handlers.admin_handlers module not available")
class TestLDAPConfigCreateHandler:
    def test_post_missing_db_renders_error(self, mock_tornado_app, mock_tornado_request):
        handler = LDAPConfigCreateHandler(mock_tornado_app, mock_tornado_request)
        authenticate(handler)
        handler.request.method = "POST"
        handler.get_argument = MagicMock(return_value="")
        with patch.object(handler, 'is_admin_user', return_value=True), \
             patch_db_conn(None), \
             patch.object(handler, 'render') as mock_render:
            handler.post()
            mock_render.assert_called_once()

    def test_post_creates_configuration(self, mock_tornado_app, mock_tornado_request, mock_db_conn):
        handler = LDAPConfigCreateHandler(mock_tornado_app, mock_tornado_request)
        authenticate(handler)
        handler.request.method = "POST"
        handler.get_argument = MagicMock(side_effect=lambda key, default=None: {
            'name': 'corp',
            'server': 'ldap://example.com',
            'ldap_base_dn': 'dc=corp,dc=com',
            'ldap_member_attributes': 'member',
            'user_template': 'uid={username},dc=corp,dc=com'
        }.get(key, default))
        with patch.object(handler, 'is_admin_user', return_value=True), \
             patch_db_conn(mock_db_conn), \
             patch('aird.handlers.admin_handlers.create_ldap_config') as mock_create_config, \
             patch.object(handler, 'redirect') as mock_redirect:
            handler.post()
            mock_create_config.assert_called_once()
            mock_redirect.assert_called_once_with("/admin/ldap")

    def test_get_not_admin_returns_403(self, mock_tornado_app, mock_tornado_request):
        handler = LDAPConfigCreateHandler(mock_tornado_app, mock_tornado_request)
        authenticate(handler, role='user')
        with patch.object(handler, 'is_admin_user', return_value=False), \
             patch.object(handler, 'set_status') as mock_status, \
             patch.object(handler, 'write') as mock_write:
            handler.get()
            mock_status.assert_called_once_with(403)
            assert mock_write.called

    def test_post_missing_required_fields(self, mock_tornado_app, mock_tornado_request, mock_db_conn):
        handler = LDAPConfigCreateHandler(mock_tornado_app, mock_tornado_request)
        authenticate(handler)
        handler.request.method = "POST"
        handler.get_argument = MagicMock(return_value="")
        with patch.object(handler, 'is_admin_user', return_value=True), \
             patch_db_conn(mock_db_conn), \
             patch.object(handler, 'render') as mock_render:
            handler.post()
            mock_render.assert_called_once()
            assert mock_render.call_args.kwargs['error'] == "All fields are required"

    def test_post_invalid_name_length(self, mock_tornado_app, mock_tornado_request, mock_db_conn):
        handler = LDAPConfigCreateHandler(mock_tornado_app, mock_tornado_request)
        authenticate(handler)
        handler.request.method = "POST"
        handler.get_argument = MagicMock(side_effect=lambda key, default=None: {
            'name': 'ab',
            'server': 'ldap://example.com',
            'ldap_base_dn': 'dc=corp,dc=com',
            'ldap_member_attributes': 'member',
            'user_template': 'uid={username},dc=corp,dc=com'
        }.get(key, default))
        with patch.object(handler, 'is_admin_user', return_value=True), \
             patch_db_conn(mock_db_conn), \
             patch.object(handler, 'render') as mock_render:
            handler.post()
            assert mock_render.call_args.kwargs['error'] == "Configuration name must be between 3 and 50 characters"

    def test_post_handles_value_error(self, mock_tornado_app, mock_tornado_request, mock_db_conn):
        handler = LDAPConfigCreateHandler(mock_tornado_app, mock_tornado_request)
        authenticate(handler)
        handler.request.method = "POST"
        handler.get_argument = MagicMock(side_effect=lambda key, default=None: {
            'name': 'corp',
            'server': 'ldap://example.com',
            'ldap_base_dn': 'dc=corp,dc=com',
            'ldap_member_attributes': 'member',
            'user_template': 'uid={username},dc=corp,dc=com'
        }.get(key, default))
        with patch.object(handler, 'is_admin_user', return_value=True), \
             patch_db_conn(mock_db_conn), \
             patch('aird.handlers.admin_handlers.create_ldap_config', side_effect=ValueError("duplicate")), \
             patch.object(handler, 'render') as mock_render:
            handler.post()
            assert mock_render.call_args.kwargs['error'] == "duplicate"

    def test_post_handles_general_exception(self, mock_tornado_app, mock_tornado_request, mock_db_conn):
        handler = LDAPConfigCreateHandler(mock_tornado_app, mock_tornado_request)
        authenticate(handler)
        handler.request.method = "POST"
        handler.get_argument = MagicMock(side_effect=lambda key, default=None: {
            'name': 'corp',
            'server': 'ldap://example.com',
            'ldap_base_dn': 'dc=corp,dc=com',
            'ldap_member_attributes': 'member',
            'user_template': 'uid={username},dc=corp,dc=com'
        }.get(key, default))
        with patch.object(handler, 'is_admin_user', return_value=True), \
             patch_db_conn(mock_db_conn), \
             patch('aird.handlers.admin_handlers.create_ldap_config', side_effect=Exception("boom")), \
             patch.object(handler, 'render') as mock_render:
            handler.post()
            assert "Error creating configuration" in mock_render.call_args.kwargs['error']


@pytest.mark.skipif(not AIRD_AVAILABLE, reason="aird.handlers.admin_handlers module not available")
class TestLDAPConfigEditHandler:
    def test_get_returns_404_when_missing(self, mock_tornado_app, mock_tornado_request, mock_db_conn):
        handler = LDAPConfigEditHandler(mock_tornado_app, mock_tornado_request)
        authenticate(handler)
        with patch.object(handler, 'is_admin_user', return_value=True), \
             patch_db_conn(mock_db_conn), \
             patch('aird.handlers.admin_handlers.get_ldap_config_by_id', return_value=None), \
             patch.object(handler, 'set_status') as mock_status, \
             patch.object(handler, 'write') as mock_write:
            handler.get("99")
            mock_status.assert_called_once_with(404)
            assert mock_write.called

    def test_post_updates_configuration(self, mock_tornado_app, mock_tornado_request, mock_db_conn):
        handler = LDAPConfigEditHandler(mock_tornado_app, mock_tornado_request)
        authenticate(handler)
        handler.request.method = "POST"
        handler.get_argument = MagicMock(side_effect=lambda key, default=None: {
            'name': 'corp',
            'server': 'ldap://example.com',
            'ldap_base_dn': 'dc=corp,dc=com',
            'ldap_member_attributes': 'member',
            'user_template': 'uid={username},dc=corp,dc=com',
            'active': 'on'
        }.get(key, default))
        config = {'id': 1, 'name': 'corp'}
        with patch.object(handler, 'is_admin_user', return_value=True), \
             patch_db_conn(mock_db_conn), \
             patch('aird.handlers.admin_handlers.get_ldap_config_by_id', return_value=config), \
             patch('aird.handlers.admin_handlers.update_ldap_config', return_value=True) as mock_update_config, \
             patch.object(handler, 'redirect') as mock_redirect:
            handler.post("1")
            mock_update_config.assert_called_once()
            mock_redirect.assert_called_once_with("/admin/ldap")

    def test_get_not_admin_denied(self, mock_tornado_app, mock_tornado_request):
        handler = LDAPConfigEditHandler(mock_tornado_app, mock_tornado_request)
        authenticate(handler, role='user')
        with patch.object(handler, 'is_admin_user', return_value=False), \
             patch.object(handler, 'set_status') as mock_status, \
             patch.object(handler, 'write') as mock_write:
            handler.get("1")
            mock_status.assert_called_once_with(403)
            assert mock_write.called

    def test_get_returns_500_when_db_missing(self, mock_tornado_app, mock_tornado_request):
        handler = LDAPConfigEditHandler(mock_tornado_app, mock_tornado_request)
        authenticate(handler)
        with patch.object(handler, 'is_admin_user', return_value=True), \
             patch_db_conn(None), \
             patch.object(handler, 'set_status') as mock_status, \
             patch.object(handler, 'write') as mock_write:
            handler.get("1")
            mock_status.assert_called_once_with(500)
            assert "Database connection error" in mock_write.call_args[0][0]

    def test_get_invalid_id_returns_error(self, mock_tornado_app, mock_tornado_request, mock_db_conn):
        handler = LDAPConfigEditHandler(mock_tornado_app, mock_tornado_request)
        authenticate(handler)
        with patch.object(handler, 'is_admin_user', return_value=True), \
             patch_db_conn(mock_db_conn), \
             patch.object(handler, 'write') as mock_write:
            handler.get("abc")
            assert "valid configuration ID" in mock_write.call_args[0][0]

    def test_post_not_admin_denied(self, mock_tornado_app, mock_tornado_request):
        handler = LDAPConfigEditHandler(mock_tornado_app, mock_tornado_request)
        authenticate(handler, role='user')
        handler.request.method = "POST"
        with patch.object(handler, 'is_admin_user', return_value=False), \
             patch.object(handler, 'set_status') as mock_status, \
             patch.object(handler, 'write') as mock_write:
            handler.post("1")
            mock_status.assert_called_once_with(403)
            assert mock_write.called

    def test_post_returns_500_when_db_missing(self, mock_tornado_app, mock_tornado_request):
        handler = LDAPConfigEditHandler(mock_tornado_app, mock_tornado_request)
        authenticate(handler)
        handler.request.method = "POST"
        with patch.object(handler, 'is_admin_user', return_value=True), \
             patch_db_conn(None), \
             patch.object(handler, 'set_status') as mock_status, \
             patch.object(handler, 'write') as mock_write:
            handler.post("1")
            mock_status.assert_called_once_with(500)
            assert "Database connection error" in mock_write.call_args[0][0]

    def test_post_returns_404_when_config_missing(self, mock_tornado_app, mock_tornado_request, mock_db_conn):
        handler = LDAPConfigEditHandler(mock_tornado_app, mock_tornado_request)
        authenticate(handler)
        handler.request.method = "POST"
        with patch.object(handler, 'is_admin_user', return_value=True), \
             patch_db_conn(mock_db_conn), \
             patch('aird.handlers.admin_handlers.get_ldap_config_by_id', return_value=None), \
             patch.object(handler, 'set_status') as mock_status, \
             patch.object(handler, 'write') as mock_write:
            handler.post("1")
            mock_status.assert_called_once_with(404)
            assert "Configuration not found" in mock_write.call_args[0][0]

    def test_post_missing_required_fields(self, mock_tornado_app, mock_tornado_request, mock_db_conn):
        handler = LDAPConfigEditHandler(mock_tornado_app, mock_tornado_request)
        authenticate(handler)
        handler.request.method = "POST"
        handler.get_argument = MagicMock(return_value="")
        config = {'id': 1, 'name': 'corp'}
        with patch.object(handler, 'is_admin_user', return_value=True), \
             patch_db_conn(mock_db_conn), \
             patch('aird.handlers.admin_handlers.get_ldap_config_by_id', return_value=config), \
             patch.object(handler, 'render') as mock_render:
            handler.post("1")
            assert mock_render.call_args.kwargs['error'] == "All fields are required"

    def test_post_invalid_name_length(self, mock_tornado_app, mock_tornado_request, mock_db_conn):
        handler = LDAPConfigEditHandler(mock_tornado_app, mock_tornado_request)
        authenticate(handler)
        handler.request.method = "POST"
        handler.get_argument = MagicMock(side_effect=lambda key, default=None: {
            'name': 'ab' if key == 'name' else 'value'
        }.get(key, 'value'))
        config = {'id': 1, 'name': 'corp'}
        with patch.object(handler, 'is_admin_user', return_value=True), \
             patch_db_conn(mock_db_conn), \
             patch('aird.handlers.admin_handlers.get_ldap_config_by_id', return_value=config), \
             patch.object(handler, 'render') as mock_render:
            handler.post("1")
            assert "between 3 and 50 characters" in mock_render.call_args.kwargs['error']

    def test_post_update_failure_renders_error(self, mock_tornado_app, mock_tornado_request, mock_db_conn):
        handler = LDAPConfigEditHandler(mock_tornado_app, mock_tornado_request)
        authenticate(handler)
        handler.request.method = "POST"
        handler.get_argument = MagicMock(return_value="value")
        config = {'id': 1, 'name': 'corp'}
        with patch.object(handler, 'is_admin_user', return_value=True), \
             patch_db_conn(mock_db_conn), \
             patch('aird.handlers.admin_handlers.get_ldap_config_by_id', return_value=config), \
             patch('aird.handlers.admin_handlers.update_ldap_config', return_value=False), \
             patch.object(handler, 'render') as mock_render:
            handler.post("1")
            assert mock_render.call_args.kwargs['error'] == "Failed to update configuration"

    def test_post_invalid_config_id_value_error(self, mock_tornado_app, mock_tornado_request, mock_db_conn):
        handler = LDAPConfigEditHandler(mock_tornado_app, mock_tornado_request)
        authenticate(handler)
        handler.request.method = "POST"
        with patch.object(handler, 'is_admin_user', return_value=True), \
             patch_db_conn(mock_db_conn), \
             patch.object(handler, 'write') as mock_write:
            handler.post("abc")
            assert "valid configuration ID" in mock_write.call_args[0][0]

    def test_post_handles_exception(self, mock_tornado_app, mock_tornado_request, mock_db_conn):
        handler = LDAPConfigEditHandler(mock_tornado_app, mock_tornado_request)
        authenticate(handler)
        handler.request.method = "POST"
        handler.get_argument = MagicMock(return_value="value")
        config = {'id': 1, 'name': 'corp'}
        with patch.object(handler, 'is_admin_user', return_value=True), \
             patch_db_conn(mock_db_conn), \
             patch('aird.handlers.admin_handlers.get_ldap_config_by_id', return_value=config), \
             patch('aird.handlers.admin_handlers.update_ldap_config', side_effect=Exception("boom")), \
             patch.object(handler, 'render') as mock_render:
            handler.post("1")
            assert "Error updating configuration" in mock_render.call_args.kwargs['error']


@pytest.mark.skipif(not AIRD_AVAILABLE, reason="aird.handlers.admin_handlers module not available")
class TestLDAPConfigDeleteHandler:
    def test_post_invalid_id_returns_400(self, mock_tornado_app, mock_tornado_request, mock_db_conn):
        handler = LDAPConfigDeleteHandler(mock_tornado_app, mock_tornado_request)
        authenticate(handler)
        handler.get_argument = MagicMock(return_value="0")
        with patch.object(handler, 'is_admin_user', return_value=True), \
             patch_db_conn(mock_db_conn), \
             patch.object(handler, 'set_status') as mock_status, \
             patch.object(handler, 'write') as mock_write:
            handler.post()
            mock_status.assert_called_once_with(400)
            assert mock_write.called

    def test_post_deletes_config(self, mock_tornado_app, mock_tornado_request, mock_db_conn):
        handler = LDAPConfigDeleteHandler(mock_tornado_app, mock_tornado_request)
        authenticate(handler)
        handler.get_argument = MagicMock(return_value="2")
        with patch.object(handler, 'is_admin_user', return_value=True), \
             patch_db_conn(mock_db_conn), \
             patch('aird.handlers.admin_handlers.delete_ldap_config', return_value=True) as mock_delete, \
             patch.object(handler, 'redirect') as mock_redirect:
            handler.post()
            mock_delete.assert_called_once_with(mock_db_conn, 2)
            mock_redirect.assert_called_once_with("/admin/ldap")

    def test_post_not_admin_denied(self, mock_tornado_app, mock_tornado_request):
        handler = LDAPConfigDeleteHandler(mock_tornado_app, mock_tornado_request)
        authenticate(handler, role='user')
        handler.get_argument = MagicMock(return_value="1")
        with patch.object(handler, 'is_admin_user', return_value=False), \
             patch.object(handler, 'set_status') as mock_status, \
             patch.object(handler, 'write') as mock_write:
            handler.post()
            mock_status.assert_called_once_with(403)
            assert mock_write.called

    def test_post_returns_500_when_db_missing(self, mock_tornado_app, mock_tornado_request):
        handler = LDAPConfigDeleteHandler(mock_tornado_app, mock_tornado_request)
        authenticate(handler)
        handler.get_argument = MagicMock(return_value="1")
        with patch.object(handler, 'is_admin_user', return_value=True), \
             patch_db_conn(None), \
             patch.object(handler, 'set_status') as mock_status, \
             patch.object(handler, 'write') as mock_write:
            handler.post()
            mock_status.assert_called_once_with(500)
            assert "Database connection error" in mock_write.call_args[0][0]

    def test_post_delete_returns_false(self, mock_tornado_app, mock_tornado_request, mock_db_conn):
        handler = LDAPConfigDeleteHandler(mock_tornado_app, mock_tornado_request)
        authenticate(handler)
        handler.get_argument = MagicMock(return_value="3")
        with patch.object(handler, 'is_admin_user', return_value=True), \
             patch_db_conn(mock_db_conn), \
             patch('aird.handlers.admin_handlers.delete_ldap_config', return_value=False), \
             patch.object(handler, 'set_status') as mock_status, \
             patch.object(handler, 'write') as mock_write:
            handler.post()
            mock_status.assert_called_once_with(404)
            assert "Configuration not found" in mock_write.call_args[0][0]

    def test_post_invalid_config_id_value_error(self, mock_tornado_app, mock_tornado_request, mock_db_conn):
        handler = LDAPConfigDeleteHandler(mock_tornado_app, mock_tornado_request)
        authenticate(handler)
        handler.get_argument = MagicMock(return_value="abc")
        with patch.object(handler, 'is_admin_user', return_value=True), \
             patch_db_conn(mock_db_conn), \
             patch.object(handler, 'set_status') as mock_status, \
             patch.object(handler, 'write') as mock_write:
            handler.post()
            mock_status.assert_called_once_with(400)
            assert "valid configuration ID" in mock_write.call_args[0][0]


@pytest.mark.skipif(not AIRD_AVAILABLE, reason="aird.handlers.admin_handlers module not available")
class TestLDAPSyncHandler:
    def test_post_non_admin_forbidden(self, mock_tornado_app, mock_tornado_request):
        handler = LDAPSyncHandler(mock_tornado_app, mock_tornado_request)
        authenticate(handler, role='user')
        with patch.object(handler, 'is_admin_user', return_value=False), \
             patch.object(handler, 'set_status') as mock_status, \
             patch.object(handler, 'write') as mock_write:
            handler.post()
            mock_status.assert_called_once_with(403)
            mock_write.assert_called_once_with({"error": "Access denied"})

    def test_post_admin_returns_status(self, mock_tornado_app, mock_tornado_request):
        handler = LDAPSyncHandler(mock_tornado_app, mock_tornado_request)
        authenticate(handler)
        with patch.object(handler, 'is_admin_user', return_value=True), \
             patch.object(handler, 'write') as mock_write:
            handler.post()
            mock_write.assert_called_once_with({"status": "Sync started"})
