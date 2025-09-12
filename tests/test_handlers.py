"""
Unit tests for HTTP handler classes in aird.main module.

These tests cover authentication handlers, base handler functionality,
and core HTTP request handling.
"""

import pytest
from unittest.mock import patch, MagicMock

# Import with error handling for missing module
try:
    from aird.main import (
        BaseHandler,
        RootHandler,
        LoginHandler,
        AdminLoginHandler,
        LogoutHandler,
        AdminHandler,
        get_relative_path
    )
    AIRD_AVAILABLE = True
except ImportError:
    AIRD_AVAILABLE = False


@pytest.mark.skipif(not AIRD_AVAILABLE, reason="aird.main module not available")
class TestUtilityFunctions:
    """Test utility functions used by handlers"""
    
    @pytest.mark.parametrize("path,root,expected", [
        ("/home/user/docs/file.txt", "/home/user", "docs/file.txt"),
        ("/home/user/file.txt", "/home/user", "file.txt"),
        ("/home/user", "/home/user", "."),
        ("/etc/passwd", "/home/user", "/etc/passwd"),  # Outside root
    ])
    def test_get_relative_path(self, path, root, expected):
        """Test get_relative_path function with various inputs"""
        result = get_relative_path(path, root)
        # Normalize path separators for cross-platform compatibility
        # Only normalize relative paths, not absolute paths that are returned unchanged
        import os
        if expected != "." and not expected.startswith('/') and not os.path.isabs(expected):
            expected = expected.replace("/", os.sep)
        assert result == expected


@pytest.mark.skipif(not AIRD_AVAILABLE, reason="aird.main module not available")
class TestBaseHandler:
    """Test BaseHandler functionality"""
    
    def test_base_handler_creation(self, mock_tornado_app, mock_tornado_request):
        """Test BaseHandler can be instantiated"""
        handler = BaseHandler(mock_tornado_app, mock_tornado_request)
        assert handler.application == mock_tornado_app
        assert handler.request == mock_tornado_request


@pytest.mark.skipif(not AIRD_AVAILABLE, reason="aird.main module not available")
class TestRootHandler:
    """Test RootHandler functionality"""
    
    def test_root_handler_redirects(self, mock_tornado_app, mock_tornado_request):
        """Test that RootHandler redirects to /files/"""
        handler = RootHandler(mock_tornado_app, mock_tornado_request)
        
        with patch.object(handler, 'redirect') as mock_redirect:
            handler.get()
            mock_redirect.assert_called_once_with("/files/")


@pytest.mark.skipif(not AIRD_AVAILABLE, reason="aird.main module not available")
class TestLogoutHandler:
    """Test LogoutHandler functionality"""
    
    def test_logout_clears_cookies(self, mock_tornado_app, mock_tornado_request):
        """Test that LogoutHandler clears authentication cookies"""
        handler = LogoutHandler(mock_tornado_app, mock_tornado_request)
        
        with patch.object(handler, 'clear_cookie') as mock_clear_cookie, \
             patch.object(handler, 'redirect') as mock_redirect:
            
            handler.get()
            
            # Should clear both user and admin cookies
            cookie_calls = [call[0][0] for call in mock_clear_cookie.call_args_list]
            assert "user" in cookie_calls
            assert "admin" in cookie_calls
            
            # Should redirect to login
            mock_redirect.assert_called_once_with("/login")


@pytest.mark.skipif(not AIRD_AVAILABLE, reason="aird.main module not available")
class TestLoginHandler:
    """Test LoginHandler functionality"""
    
    def test_login_handler_get_not_authenticated(self, mock_tornado_app, mock_tornado_request):
        """Test LoginHandler GET when user is not authenticated"""
        handler = LoginHandler(mock_tornado_app, mock_tornado_request)
        
        with patch.object(handler, 'get_current_user', return_value=None), \
             patch.object(handler, 'render') as mock_render:
            
            handler.get()
            
            # Should render login template
            mock_render.assert_called_once()
            args, kwargs = mock_render.call_args
            assert 'login.html' in args
    
    def test_login_handler_get_already_authenticated(self, mock_tornado_app, mock_tornado_request):
        """Test LoginHandler GET when user is already authenticated"""
        handler = LoginHandler(mock_tornado_app, mock_tornado_request)
        
        with patch.object(handler, 'get_current_user', return_value='test_user'), \
             patch.object(handler, 'redirect') as mock_redirect:
            
            handler.get()
            
            # Should redirect to files page
            mock_redirect.assert_called_once_with("/files/")


@pytest.mark.skipif(not AIRD_AVAILABLE, reason="aird.main module not available")
class TestAdminLoginHandler:
    """Test AdminLoginHandler functionality"""
    
    def test_admin_login_handler_get_not_authenticated(self, mock_tornado_app, mock_tornado_request):
        """Test AdminLoginHandler GET when admin is not authenticated"""
        handler = AdminLoginHandler(mock_tornado_app, mock_tornado_request)
        
        with patch.object(handler, 'get_current_admin', return_value=None), \
             patch.object(handler, 'render') as mock_render:
            
            handler.get()
            
            # Should render admin login template
            mock_render.assert_called_once()
            args, kwargs = mock_render.call_args
            assert 'admin_login.html' in args


@pytest.mark.skipif(not AIRD_AVAILABLE, reason="aird.main module not available")
class TestAdminHandler:
    """Test AdminHandler functionality"""
    
    def test_admin_handler_get_authenticated(self, mock_tornado_app, mock_tornado_request):
        """Test AdminHandler GET when admin is authenticated"""
        handler = AdminHandler(mock_tornado_app, mock_tornado_request)
        
        with patch.object(handler, 'get_current_user', return_value='user'), \
             patch.object(handler, 'get_current_admin', return_value='admin'), \
             patch('aird.main.get_current_feature_flags', return_value={'test': True}), \
             patch('aird.main.RUST_AVAILABLE', False), \
             patch.object(handler, 'render') as mock_render:
            
            handler.get()
            
            # Should render admin template
            mock_render.assert_called_once()
            args, kwargs = mock_render.call_args
            assert 'admin.html' in args
    
    def test_admin_handler_get_not_authenticated(self, mock_tornado_app, mock_tornado_request):
        """Test AdminHandler GET when admin is not authenticated"""
        handler = AdminHandler(mock_tornado_app, mock_tornado_request)
        
        with patch.object(handler, 'get_current_user', return_value='user'), \
             patch.object(handler, 'get_current_admin', return_value=None), \
             patch.object(handler, 'redirect') as mock_redirect:
            
            handler.get()
            
            # Should redirect to admin login
            mock_redirect.assert_called_once_with("/admin/login")


@pytest.mark.skipif(not AIRD_AVAILABLE, reason="aird.main module not available")
class TestSuperSearchHandler:
    """Test SuperSearchHandler functionality"""
    
    def test_super_search_handler_enabled(self, mock_tornado_app, mock_tornado_request):
        """Test SuperSearchHandler when feature is enabled"""
        from aird.main import SuperSearchHandler
        handler = SuperSearchHandler(mock_tornado_app, mock_tornado_request)
        
        with patch.object(handler, 'get_current_user', return_value='user'), \
             patch('aird.main.is_feature_enabled', return_value=True), \
             patch.object(handler, 'render') as mock_render:
            
            handler.get()
            
            # Should render super search template
            mock_render.assert_called_once()
            args, kwargs = mock_render.call_args
            assert 'super_search.html' in args
    
    def test_super_search_handler_disabled(self, mock_tornado_app, mock_tornado_request):
        """Test SuperSearchHandler when feature is disabled"""
        from aird.main import SuperSearchHandler
        handler = SuperSearchHandler(mock_tornado_app, mock_tornado_request)
        
        with patch.object(handler, 'get_current_user', return_value='user'), \
             patch('aird.main.is_feature_enabled', return_value=False), \
             patch.object(handler, 'set_status') as mock_set_status, \
             patch.object(handler, 'write') as mock_write:
            
            handler.get()
            
            # Should return 403 Forbidden
            mock_set_status.assert_called_once_with(403)
            mock_write.assert_called_once_with("Super search is disabled.")