
import pytest
from unittest.mock import patch, MagicMock
from aird.handlers.auth_handlers import LoginHandler, LogoutHandler, AdminLoginHandler, ProfileHandler, LDAPLoginHandler
import json

from tests.handler_helpers import authenticate, patch_db_conn, prepare_handler

class TestLoginHandler:
    def setup_method(self):
        self.mock_app = MagicMock()
        self.mock_request = MagicMock()
        self.mock_app.settings = {
            'cookie_secret': 'test_secret',
            'login_url': '/login',
            'debug': False
        }
        self.mock_request.connection = MagicMock()
        self.mock_request.protocol = "http"

    def test_get_login_page(self):
        handler = prepare_handler(LoginHandler(self.mock_app, self.mock_request))
        with patch.object(handler, 'render') as mock_render:
            handler.get()
            mock_render.assert_called_once()
            args, kwargs = mock_render.call_args
            assert args[0] == "login.html"

    def test_get_redirects_if_authenticated(self):
        handler = prepare_handler(LoginHandler(self.mock_app, self.mock_request))
        authenticate(handler, role='user')
        with patch.object(handler, 'redirect') as mock_redirect:
            handler.get()
            mock_redirect.assert_called_once()

    def test_post_login_success(self):
        handler = prepare_handler(LoginHandler(self.mock_app, self.mock_request))
        handler.get_argument = MagicMock(side_effect=lambda k, d=None: {'username': 'user', 'password': 'password'}.get(k, d))
        
        with patch_db_conn(MagicMock(), modules=['aird.handlers.auth_handlers']), \
             patch('aird.handlers.auth_handlers.authenticate_user', return_value={'username': 'user', 'role': 'user'}), \
             patch.object(handler, 'set_secure_cookie') as mock_cookie, \
             patch.object(handler, 'redirect') as mock_redirect:
            
            handler.post()
            assert mock_cookie.call_count == 2 # user and role
            mock_redirect.assert_called_with('/files/')

    def test_post_login_failure(self):
        handler = prepare_handler(LoginHandler(self.mock_app, self.mock_request))
        handler.get_argument = MagicMock(side_effect=lambda k, d=None: {'username': 'user', 'password': 'wrong'}.get(k, d))
        
        with patch_db_conn(MagicMock(), modules=['aird.handlers.auth_handlers']), \
             patch('aird.handlers.auth_handlers.authenticate_user', return_value=None), \
             patch.object(handler, 'render') as mock_render:
            
            handler.post()
            mock_render.assert_called()
            assert mock_render.call_args[1]['error'] == "Invalid username or password."

class TestLogoutHandler:
    def setup_method(self):
        self.mock_app = MagicMock()
        self.mock_request = MagicMock()
        self.mock_app.settings = {'cookie_secret': 'test_secret'}
    
    def test_logout(self):
        handler = prepare_handler(LogoutHandler(self.mock_app, self.mock_request))
        with patch.object(handler, 'clear_cookie') as mock_clear, \
             patch.object(handler, 'redirect') as mock_redirect:
            handler.get()
            assert mock_clear.call_count == 2 # user and admin
            mock_redirect.assert_called_with('/login')

class TestAdminLoginHandler:
    def setup_method(self):
        self.mock_app = MagicMock()
        self.mock_request = MagicMock()
        self.mock_app.settings = {'cookie_secret': 'test_secret'}
        self.mock_request.protocol = "http"

    def test_get_admin_login(self):
        handler = prepare_handler(AdminLoginHandler(self.mock_app, self.mock_request))
        with patch.object(handler, 'is_admin_user', return_value=False), \
             patch.object(handler, 'render') as mock_render:
            handler.get()
            mock_render.assert_called_with("admin_login.html", error=None)

    def test_post_admin_login_success(self):
        handler = prepare_handler(AdminLoginHandler(self.mock_app, self.mock_request))
        handler.get_argument = MagicMock(side_effect=lambda k, d=None: {'username': 'admin', 'password': 'password'}.get(k, d))
        
        with patch_db_conn(MagicMock(), modules=['aird.handlers.auth_handlers']), \
             patch('aird.handlers.auth_handlers.authenticate_user', return_value={'username': 'admin', 'role': 'admin'}), \
             patch.object(handler, 'set_secure_cookie') as mock_cookie, \
             patch.object(handler, 'redirect') as mock_redirect:
            
            handler.post()
            assert mock_cookie.call_count == 3 # user, role, admin
            mock_redirect.assert_called_with('/admin')

class TestProfileHandler:
    def setup_method(self):
        self.mock_app = MagicMock()
        self.mock_request = MagicMock()
        self.mock_app.settings = {'cookie_secret': 'test_secret'}
        self.mock_request.protocol = "http"

    def test_get_profile(self):
        handler = prepare_handler(ProfileHandler(self.mock_app, self.mock_request))
        authenticate(handler, role='user')
        with patch.object(handler, 'render') as mock_render:
            handler.get()
            mock_render.assert_called()
            assert mock_render.call_args[0][0] == "profile.html"

    def test_post_update_password(self):
        handler = prepare_handler(ProfileHandler(self.mock_app, self.mock_request))
        authenticate(handler, role='user', username='user')
        handler.get_argument = MagicMock(side_effect=lambda k, d=None: {'new_password': 'newpass', 'confirm_password': 'newpass'}.get(k, d))
        
        with patch_db_conn(MagicMock(), modules=['aird.handlers.auth_handlers']), \
             patch('aird.handlers.auth_handlers.get_user_by_username', return_value={'id': 1, 'username': 'user'}), \
             patch('aird.handlers.auth_handlers.update_user') as mock_update, \
             patch.object(handler, 'render') as mock_render:
            
            handler.post()
            mock_update.assert_called()
            assert mock_render.call_args[1]['success'] == "Password updated successfully"

class TestLDAPLoginHandler:
    def setup_method(self):
        self.mock_app = MagicMock()
        self.mock_request = MagicMock()
        self.mock_app.settings = {
            'cookie_secret': 'test_secret',
            'ldap_server': 'ldap://localhost',
            'ldap_user_template': 'uid={username},ou=users,dc=example,dc=com',
            'ldap_base_dn': 'ou=users,dc=example,dc=com',
            'ldap_filter_template': '(uid={username})',
            'ldap_attributes': ['cn', 'mail', 'uid'],
            'ldap_attribute_map': []
        }
        self.mock_request.protocol = "http"

    def test_get_ldap_login(self):
        handler = prepare_handler(LDAPLoginHandler(self.mock_app, self.mock_request))
        with patch.object(handler, 'render') as mock_render:
            handler.get()
            mock_render.assert_called_with("login.html", error=None, settings=self.mock_app.settings)

    def test_post_ldap_login_success(self):
        handler = prepare_handler(LDAPLoginHandler(self.mock_app, self.mock_request))
        handler.get_argument = MagicMock(side_effect=lambda k, d=None: {'username': 'user', 'password': 'password'}.get(k, d))
        
        mock_conn = MagicMock()
        mock_conn.search.return_value = True
        mock_conn.entries = [MagicMock(entry_attributes_as_dict={'uid': ['user'], 'mail': ['user@example.com'], 'cn': ['User Name']})]
        
        with patch('aird.handlers.auth_handlers.Server'), \
             patch('aird.handlers.auth_handlers.Connection', return_value=mock_conn), \
             patch_db_conn(MagicMock(), modules=['aird.handlers.auth_handlers']), \
             patch('aird.handlers.auth_handlers.get_user_by_username', return_value=None), \
             patch('aird.handlers.auth_handlers.create_user') as mock_create, \
             patch.object(handler, 'set_secure_cookie') as mock_cookie, \
             patch.object(handler, 'redirect') as mock_redirect:
            
            handler.post()
            mock_create.assert_called()
            mock_cookie.assert_called()
            mock_redirect.assert_called_with('/files/')
