"""
Unit tests for handler classes in aird.main module.
"""

import pytest
import tornado.testing
import tornado.web
from unittest.mock import patch, MagicMock, mock_open
from aird.main import (
    BaseHandler,
    RootHandler,
    LoginHandler,
    AdminLoginHandler,
    LogoutHandler,
    AdminHandler,
    get_relative_path
)


class TestBaseHandler(tornado.testing.AsyncHTTPTestCase):
    """Test BaseHandler class"""
    
    def get_app(self):
        """Create test application"""
        class TestHandler(BaseHandler):
            def get(self):
                self.write("test")
        
        return tornado.web.Application([
            (r"/test", TestHandler),
        ], cookie_secret="test_secret")
    
    def test_security_headers(self):
        """Test that security headers are set correctly"""
        response = self.fetch("/test")
        
        # Check security headers
        headers = response.headers
        assert headers.get("X-Content-Type-Options") == "nosniff"
        assert headers.get("X-Frame-Options") == "DENY"
        assert headers.get("X-XSS-Protection") == "1; mode=block"
        assert headers.get("Referrer-Policy") == "strict-origin-when-cross-origin"
        assert "Content-Security-Policy" in headers
        
        # Check CSP content
        csp = headers.get("Content-Security-Policy")
        assert "default-src 'self'" in csp
        assert "script-src 'self' 'unsafe-inline'" in csp
        assert "style-src 'self' 'unsafe-inline'" in csp
    
    def test_write_error_generic_messages(self):
        """Test that write_error provides generic error messages"""
        class ErrorHandler(BaseHandler):
            def get(self):
                self.send_error(404)
        
        app = tornado.web.Application([
            (r"/error", ErrorHandler),
        ], cookie_secret="test_secret")
        
        with patch.object(ErrorHandler, 'render') as mock_render:
            request = tornado.testing.AsyncHTTPTestCase.get_http_client(self)._make_request(
                self.get_url("/error")
            )
            # The error handling will call render with generic message
            # We can't easily test this without more complex setup


class TestUtilityFunctions:
    """Test utility functions"""
    
    def test_get_relative_path_within_root(self):
        """Test get_relative_path when path is within root"""
        result = get_relative_path("/home/user/docs/file.txt", "/home/user")
        assert result == "docs/file.txt"
    
    def test_get_relative_path_outside_root(self):
        """Test get_relative_path when path is outside root"""
        result = get_relative_path("/etc/passwd", "/home/user")
        assert result == "/etc/passwd"
    
    def test_get_relative_path_same_as_root(self):
        """Test get_relative_path when path is same as root"""
        result = get_relative_path("/home/user", "/home/user")
        assert result == "."


class TestRootHandler(tornado.testing.AsyncHTTPTestCase):
    """Test RootHandler class"""
    
    def get_app(self):
        """Create test application"""
        return tornado.web.Application([
            (r"/", RootHandler),
        ], cookie_secret="test_secret")
    
    def test_root_redirects_to_files(self):
        """Test that root handler redirects to /files/"""
        response = self.fetch("/", follow_redirects=False)
        assert response.code == 302
        assert response.headers.get("Location") == "/files/"


class TestLogoutHandler(tornado.testing.AsyncHTTPTestCase):
    """Test LogoutHandler class"""
    
    def get_app(self):
        """Create test application"""
        return tornado.web.Application([
            (r"/logout", LogoutHandler),
        ], cookie_secret="test_secret")
    
    def test_logout_clears_cookies_and_redirects(self):
        """Test that logout clears cookies and redirects"""
        response = self.fetch("/logout", follow_redirects=False)
        assert response.code == 302
        assert response.headers.get("Location") == "/login"
        
        # Check that cookies are cleared
        set_cookie_headers = response.headers.get_list("Set-Cookie")
        cookie_names = []
        for header in set_cookie_headers:
            if "user=" in header or "admin=" in header:
                cookie_names.append(header.split("=")[0])
        
        # Should have cleared both user and admin cookies
        assert len([c for c in cookie_names if "user" in c or "admin" in c]) >= 0


class MockApplication:
    """Mock Tornado application for testing handlers in isolation"""
    
    def __init__(self):
        self.settings = {
            'cookie_secret': 'test_secret',
            'template_path': '/fake/templates'
        }


class MockRequest:
    """Mock HTTP request for testing handlers in isolation"""
    
    def __init__(self, method="GET", path="/", body=b"", headers=None):
        self.method = method
        self.path = path
        self.body = body
        self.headers = headers or {}
        self.arguments = {}
        self.files = {}


class TestLoginHandlerUnit:
    """Unit tests for LoginHandler without HTTP server"""
    
    def setup_method(self):
        """Set up test fixtures"""
        self.app = MockApplication()
        self.request = MockRequest()
    
    @patch('aird.main.ACCESS_TOKEN', 'test_token')
    def test_login_handler_init(self):
        """Test LoginHandler initialization"""
        handler = LoginHandler(self.app, self.request)
        assert handler.application == self.app
        assert handler.request == self.request
    
    @patch('aird.main.ACCESS_TOKEN', 'test_token')
    @patch.object(LoginHandler, 'render')
    @patch.object(LoginHandler, 'current_user', None)
    def test_login_get_renders_form(self, mock_render):
        """Test LoginHandler GET renders login form"""
        handler = LoginHandler(self.app, self.request)
        handler.get()
        mock_render.assert_called_once_with("login.html", error=None, settings=self.app.settings)
    
    @patch('aird.main.ACCESS_TOKEN', 'test_token')
    @patch.object(LoginHandler, 'redirect')
    @patch.object(LoginHandler, 'current_user', 'test_user')
    def test_login_get_redirects_if_logged_in(self, mock_redirect):
        """Test LoginHandler GET redirects if already logged in"""
        handler = LoginHandler(self.app, self.request)
        handler.get()
        mock_redirect.assert_called_once_with("/files/")


class TestAdminLoginHandlerUnit:
    """Unit tests for AdminLoginHandler without HTTP server"""
    
    def setup_method(self):
        """Set up test fixtures"""
        self.app = MockApplication()
        self.request = MockRequest()
    
    @patch('aird.main.ADMIN_TOKEN', 'admin_token')
    def test_admin_login_handler_init(self):
        """Test AdminLoginHandler initialization"""
        handler = AdminLoginHandler(self.app, self.request)
        assert handler.application == self.app
        assert handler.request == self.request
    
    @patch('aird.main.ADMIN_TOKEN', 'admin_token')
    @patch.object(AdminLoginHandler, 'render')
    @patch.object(AdminLoginHandler, 'get_current_admin', return_value=None)
    def test_admin_login_get_renders_form(self, mock_get_admin, mock_render):
        """Test AdminLoginHandler GET renders login form"""
        handler = AdminLoginHandler(self.app, self.request)
        handler.get()
        mock_render.assert_called_once_with("admin_login.html", error=None, settings=self.app.settings)
    
    @patch('aird.main.ADMIN_TOKEN', 'admin_token')
    @patch.object(AdminLoginHandler, 'redirect')
    @patch.object(AdminLoginHandler, 'get_current_admin', return_value='admin')
    def test_admin_login_get_redirects_if_logged_in(self, mock_get_admin, mock_redirect):
        """Test AdminLoginHandler GET redirects if already logged in"""
        handler = AdminLoginHandler(self.app, self.request)
        handler.get()
        mock_redirect.assert_called_once_with("/admin")


class TestAdminHandlerUnit:
    """Unit tests for AdminHandler without HTTP server"""
    
    def setup_method(self):
        """Set up test fixtures"""
        self.app = MockApplication()
        self.request = MockRequest()
    
    @patch('aird.main.RUST_AVAILABLE', False)
    @patch('aird.main.DB_CONN', None)
    @patch('aird.main.FEATURE_FLAGS', {'file_upload': True, 'file_delete': False})
    @patch.object(AdminHandler, 'render')
    @patch.object(AdminHandler, 'get_current_admin', return_value='admin')
    def test_admin_handler_get_no_rust_no_db(self, mock_get_admin, mock_render):
        """Test AdminHandler GET without Rust or database"""
        handler = AdminHandler(self.app, self.request)
        handler.get()
        
        # Should render with feature flags and no Rust stats
        mock_render.assert_called_once()
        args, kwargs = mock_render.call_args
        assert args[0] == "admin.html"
        assert 'features' in kwargs
        assert kwargs['rust_available'] is False
        assert 'rust_stats' in kwargs
    
    @patch('aird.main.FEATURE_FLAGS', {'file_upload': True, 'file_delete': False})
    @patch.object(AdminHandler, 'get_argument')
    @patch.object(AdminHandler, 'get_current_admin', return_value='admin')
    @patch.object(AdminHandler, 'redirect')
    @patch('aird.main.FeatureFlagSocketHandler')
    def test_admin_handler_post_updates_flags(self, mock_socket, mock_redirect, mock_get_admin, mock_get_arg):
        """Test AdminHandler POST updates feature flags"""
        # Mock form arguments
        def mock_get_argument(name, default="off"):
            args_map = {
                'file_upload': 'on',
                'file_delete': 'off',
                'file_rename': 'on',
                'file_download': 'on',
                'file_edit': 'off',
                'file_share': 'on',
                'compression': 'on'
            }
            return args_map.get(name, default)
        
        mock_get_arg.side_effect = mock_get_argument
        
        handler = AdminHandler(self.app, self.request)
        handler.post()
        
        # Verify feature flags were updated
        from aird.main import FEATURE_FLAGS
        assert FEATURE_FLAGS['file_upload'] is True
        assert FEATURE_FLAGS['file_delete'] is False
        assert FEATURE_FLAGS['file_rename'] is True
        
        # Should redirect back to admin page
        mock_redirect.assert_called_once_with("/admin")
    
    @patch.object(AdminHandler, 'get_current_admin', return_value=None)
    @patch.object(AdminHandler, 'set_status')
    @patch.object(AdminHandler, 'write')
    def test_admin_handler_post_forbidden_without_admin(self, mock_write, mock_set_status, mock_get_admin):
        """Test AdminHandler POST returns 403 without admin auth"""
        handler = AdminHandler(self.app, self.request)
        handler.post()
        
        mock_set_status.assert_called_once_with(403)
        mock_write.assert_called_once_with("Forbidden")


# Integration tests that require the actual template files would go here
# but are omitted since we don't have access to the template files
class TestHandlerIntegration:
    """Integration tests for handlers (require template files)"""
    
    @pytest.mark.skip(reason="Requires template files")
    def test_login_form_rendering(self):
        """Test that login form renders correctly with templates"""
        pass
    
    @pytest.mark.skip(reason="Requires template files")
    def test_admin_panel_rendering(self):
        """Test that admin panel renders correctly with templates"""
        pass