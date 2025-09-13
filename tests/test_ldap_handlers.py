#!/usr/bin/env python3
"""
Tests for LDAP authentication handlers.

This module tests LDAP login functionality and related authentication flows.
"""

import pytest
from unittest.mock import MagicMock, patch, AsyncMock
import tornado.testing
import tornado.web

# Try to import from aird.main, skip tests if not available
try:
    from aird.main import LDAPLoginHandler, make_app
    AIRD_AVAILABLE = True
except ImportError:
    AIRD_AVAILABLE = False


@pytest.mark.skipif(not AIRD_AVAILABLE, reason="aird.main module not available")
class TestLDAPLoginHandler:
    """Test LDAP authentication functionality"""

    def setup_method(self):
        """Setup test fixtures"""
        self.app_settings = {
            'template_path': 'templates',
            'cookie_secret': 'test_secret',
            'ldap_server': 'ldap://test.example.com',
            'ldap_base_dn': 'dc=example,dc=com',
            'login_url': '/login'
        }

    @patch('conftest.mock_tornado_app')
    @patch('conftest.mock_tornado_request')
    def test_ldap_handler_creation(self, mock_request, mock_app):
        """Test LDAP handler can be created"""
        mock_app.settings = self.app_settings
        handler = LDAPLoginHandler(mock_app, mock_request)
        assert handler is not None

    @patch('conftest.mock_tornado_app')
    @patch('conftest.mock_tornado_request')
    def test_get_authenticated_user_redirects(self, mock_request, mock_app):
        """Test GET request when user is already authenticated"""
        mock_app.settings = self.app_settings
        handler = LDAPLoginHandler(mock_app, mock_request)
        handler.current_user = "test_user"
        handler.redirect = MagicMock()
        
        handler.get()
        handler.redirect.assert_called_with("/files/")

    @patch('conftest.mock_tornado_app')
    @patch('conftest.mock_tornado_request')
    def test_get_not_authenticated_renders_login(self, mock_request, mock_app):
        """Test GET request when user is not authenticated"""
        mock_app.settings = self.app_settings
        handler = LDAPLoginHandler(mock_app, mock_request)
        handler.current_user = None
        handler.render = MagicMock()
        
        handler.get()
        handler.render.assert_called_with("login.html", error=None, settings=self.app_settings)

    @patch('conftest.mock_tornado_app')
    @patch('conftest.mock_tornado_request')
    @patch('aird.main.Server')
    @patch('aird.main.Connection')
    def test_post_successful_ldap_authentication(self, mock_connection_class, mock_server_class, mock_request, mock_app):
        """Test successful LDAP authentication"""
        mock_app.settings = self.app_settings
        handler = LDAPLoginHandler(mock_app, mock_request)
        handler.get_argument = MagicMock()
        handler.get_argument.side_effect = lambda name, default="": {
            "username": "testuser",
            "password": "testpass"
        }.get(name, default)
        
        # Mock successful LDAP connection
        mock_connection = MagicMock()
        mock_connection.bind.return_value = True
        # Mock the Connection constructor to return our mock
        mock_connection_class.return_value = mock_connection
        
        handler.set_secure_cookie = MagicMock()
        handler.redirect = MagicMock()
        handler.render = MagicMock()  # Mock render to avoid template file issues
        
        handler.post()
        
        handler.set_secure_cookie.assert_called_with("user", "testuser")
        handler.redirect.assert_called_with("/files/")

    @patch('conftest.mock_tornado_app')
    @patch('conftest.mock_tornado_request')
    @patch('aird.main.Server')
    @patch('aird.main.Connection')
    def test_post_failed_ldap_authentication(self, mock_connection_class, mock_server_class, mock_request, mock_app):
        """Test failed LDAP authentication"""
        mock_app.settings = self.app_settings
        handler = LDAPLoginHandler(mock_app, mock_request)
        handler.get_argument = MagicMock()
        handler.get_argument.side_effect = lambda name, default="": {
            "username": "testuser",
            "password": "wrongpass"
        }.get(name, default)
        
        # Mock failed LDAP connection
        mock_connection = MagicMock()
        mock_connection.bind.return_value = False
        # Mock the Connection constructor to return our mock
        mock_connection_class.return_value = mock_connection
        
        handler.render = MagicMock()
        handler.set_secure_cookie = MagicMock()
        handler.redirect = MagicMock()
        
        handler.post()
        
        handler.render.assert_called_with("login.html", error="Invalid username or password.", settings=self.app_settings)

    @patch('conftest.mock_tornado_app')
    @patch('conftest.mock_tornado_request')
    def test_post_missing_credentials(self, mock_request, mock_app):
        """Test POST with missing username or password"""
        mock_app.settings = self.app_settings
        handler = LDAPLoginHandler(mock_app, mock_request)
        handler.get_argument = MagicMock()
        handler.get_argument.side_effect = lambda name, default="": {
            "username": "",
            "password": "testpass"
        }.get(name, default)
        
        handler.render = MagicMock()
        
        handler.post()
        
        handler.render.assert_called_with("login.html", error="Username and password are required.", settings=self.app_settings)

    @patch('conftest.mock_tornado_app')
    @patch('conftest.mock_tornado_request')
    @patch('aird.main.Server')
    @patch('aird.main.Connection')
    def test_post_ldap_connection_error(self, mock_connection_class, mock_server_class, mock_request, mock_app):
        """Test LDAP connection error handling"""
        mock_app.settings = self.app_settings
        handler = LDAPLoginHandler(mock_app, mock_request)
        handler.get_argument = MagicMock()
        handler.get_argument.side_effect = lambda name, default="": {
            "username": "testuser",
            "password": "testpass"
        }.get(name, default)
        
        # Mock LDAP connection that raises exception
        mock_connection_class.side_effect = Exception("LDAP server unavailable")
        
        handler.render = MagicMock()
        
        handler.post()
        
        handler.render.assert_called_with("login.html", error="Authentication failed. Please check your credentials.", settings=self.app_settings)

    def test_make_app_with_ldap_enabled(self):
        """Test application creation with LDAP enabled"""
        settings = {
            'cookie_secret': 'test_secret',
            'template_path': 'templates'
        }
        
        app = make_app(
            settings, 
            ldap_enabled=True, 
            ldap_server='ldap://test.com', 
            ldap_base_dn='dc=test,dc=com'
        )
        
        assert app is not None
        assert 'ldap_server' in app.settings
        assert 'ldap_base_dn' in app.settings
        assert app.settings['ldap_server'] == 'ldap://test.com'
        assert app.settings['ldap_base_dn'] == 'dc=test,dc=com'

    def test_make_app_with_ldap_disabled(self):
        """Test application creation with LDAP disabled"""
        settings = {
            'cookie_secret': 'test_secret',
            'template_path': 'templates'
        }
        
        app = make_app(settings, ldap_enabled=False)
        
        assert app is not None
        assert 'ldap_server' not in app.settings
        assert 'ldap_base_dn' not in app.settings
