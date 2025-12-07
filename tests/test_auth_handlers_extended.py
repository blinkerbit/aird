import pytest
from unittest.mock import patch, MagicMock, ANY
import tornado.web
from aird.handlers.auth_handlers import LDAPLoginHandler, LoginHandler, AdminLoginHandler, ProfileHandler, LogoutHandler
from aird.handlers.base_handler import BaseHandler
import json
from datetime import datetime

class TestLDAPLoginHandlerExtended:
    def setup_method(self):
        self.mock_app = MagicMock()
        self.mock_request = MagicMock()
        self.mock_app.settings = {
            'cookie_secret': 'test_secret',
            'ldap_server': 'ldap://localhost',
            'ldap_user_template': 'uid={username},ou=users,dc=example,dc=com',
            'ldap_base_dn': 'ou=users,dc=example,dc=com',
            'ldap_filter_template': '(uid={username})',
            'ldap_attributes': ['cn', 'mail'],
            'ldap_attribute_map': [{'member': 'cn=admin,dc=example,dc=com'}]
        }

    def test_post_input_validation_length(self):
        handler = LDAPLoginHandler(self.mock_app, self.mock_request)
        handler.get_argument = MagicMock(side_effect=lambda k, d=None: "a" * 257 if k == "username" else "password")
        
        with patch.object(handler, 'render') as mock_render:
            handler.post()
            mock_render.assert_called_with("login.html", error="Invalid input length.", settings=ANY)

    def test_post_ldap_authorization_failure(self):
        handler = LDAPLoginHandler(self.mock_app, self.mock_request)
        handler.get_argument = MagicMock(side_effect=lambda k, d=None: "user" if k == "username" else "password")
        
        # Mock LDAP connection and search
        with patch('aird.handlers.auth_handlers.Server'), \
             patch('aird.handlers.auth_handlers.Connection') as mock_conn_cls:
            
            mock_conn = MagicMock()
            mock_conn_cls.return_value = mock_conn
            # Mock entries returned by search - missing the required attribute
            mock_conn.entries = [{'member': 'cn=other,dc=example,dc=com'}]
            
            with patch.object(handler, 'render') as mock_render:
                handler.post()
                mock_render.assert_called_with("login.html", error="Access denied. You do not have permission to access this system.", settings=ANY)

    def test_post_ldap_user_creation_failure(self):
        handler = LDAPLoginHandler(self.mock_app, self.mock_request)
        handler.get_argument = MagicMock(side_effect=lambda k, d=None: "newuser" if k == "username" else "password")
        
        # Mock LDAP success
        with patch('aird.handlers.auth_handlers.Server'), \
             patch('aird.handlers.auth_handlers.Connection') as mock_conn_cls:
            
            mock_conn = MagicMock()
            mock_conn_cls.return_value = mock_conn
            mock_conn.entries = [{'member': 'cn=admin,dc=example,dc=com'}] # Authorized
            
            # Mock DB interactions
            with patch('aird.handlers.auth_handlers.constants_module.DB_CONN', MagicMock()), \
                 patch('aird.handlers.auth_handlers.get_user_by_username', return_value=None), \
                 patch('aird.handlers.auth_handlers.create_user', side_effect=Exception("DB Error")), \
                 patch.object(handler, 'set_secure_cookie') as mock_cookie, \
                 patch.object(handler, 'redirect') as mock_redirect:
                
                handler.post()
                # Should still redirect even if user creation fails
                mock_redirect.assert_called_with("/files/")

    def test_post_ldap_user_update_failure(self):
        handler = LDAPLoginHandler(self.mock_app, self.mock_request)
        handler.get_argument = MagicMock(side_effect=lambda k, d=None: "existinguser" if k == "username" else "password")
        
        # Mock LDAP success
        with patch('aird.handlers.auth_handlers.Server'), \
             patch('aird.handlers.auth_handlers.Connection') as mock_conn_cls:
            
            mock_conn = MagicMock()
            mock_conn_cls.return_value = mock_conn
            mock_conn.entries = [{'member': 'cn=admin,dc=example,dc=com'}] # Authorized
            
            # Mock DB interactions
            with patch('aird.handlers.auth_handlers.constants_module.DB_CONN', MagicMock()), \
                 patch('aird.handlers.auth_handlers.get_user_by_username', return_value={'id': 1, 'role': 'user'}), \
                 patch('aird.handlers.auth_handlers.update_user', side_effect=Exception("DB Error")), \
                 patch.object(handler, 'set_secure_cookie') as mock_cookie, \
                 patch.object(handler, 'redirect') as mock_redirect:
                
                handler.post()
                mock_redirect.assert_called_with("/files/")

    def test_post_ldap_success_new_user(self):
        handler = LDAPLoginHandler(self.mock_app, self.mock_request)
        handler.get_argument = MagicMock(side_effect=lambda k, d=None: "newuser" if k == "username" else "password")
        
        with patch('aird.handlers.auth_handlers.Server'), \
             patch('aird.handlers.auth_handlers.Connection') as mock_conn_cls:
            
            mock_conn = MagicMock()
            mock_conn_cls.return_value = mock_conn
            mock_conn.entries = [{'member': 'cn=admin,dc=example,dc=com'}]
            
            with patch('aird.handlers.auth_handlers.constants_module.DB_CONN', MagicMock()), \
                 patch('aird.handlers.auth_handlers.get_user_by_username', side_effect=[None, {'role': 'user'}]), \
                 patch('aird.handlers.auth_handlers.create_user') as mock_create, \
                 patch.object(handler, 'set_secure_cookie') as mock_cookie, \
                 patch.object(handler, 'redirect') as mock_redirect:
                
                handler.post()
                mock_create.assert_called()
                mock_redirect.assert_called_with("/files/")

    def test_post_ldap_success_existing_user_admin_update(self):
        handler = LDAPLoginHandler(self.mock_app, self.mock_request)
        handler.get_argument = MagicMock(side_effect=lambda k, d=None: "adminuser" if k == "username" else "password")
        self.mock_app.settings['admin_users'] = ['adminuser']
        
        with patch('aird.handlers.auth_handlers.Server'), \
             patch('aird.handlers.auth_handlers.Connection') as mock_conn_cls:
            
            mock_conn = MagicMock()
            mock_conn_cls.return_value = mock_conn
            mock_conn.entries = [{'member': 'cn=admin,dc=example,dc=com'}]
            
            with patch('aird.handlers.auth_handlers.constants_module.DB_CONN', MagicMock()), \
                 patch('aird.handlers.auth_handlers.get_user_by_username', return_value={'id': 1, 'role': 'user'}), \
                 patch('aird.handlers.auth_handlers.update_user') as mock_update, \
                 patch.object(handler, 'set_secure_cookie') as mock_cookie, \
                 patch.object(handler, 'redirect') as mock_redirect:
                
                handler.post()
                # Should update role to admin
                assert any(call[1].get('role') == 'admin' for call in mock_update.call_args_list)
                mock_redirect.assert_called_with("/files/")

class TestLoginHandlerExtended:
    def setup_method(self):
        self.mock_app = MagicMock()
        self.mock_request = MagicMock()
        self.mock_app.settings = {'cookie_secret': 'test_secret'}

    def test_post_form_parsing_error(self):
        handler = LoginHandler(self.mock_app, self.mock_request)
        handler.get_argument = MagicMock(side_effect=Exception("Form Error"))
        
        with patch.object(handler, 'render') as mock_render:
            handler.post()
            mock_render.assert_called_with("login.html", error="Error processing login request. Please try again.", settings=ANY, next_url="/files/")

    def test_post_token_mismatch_logging(self):
        handler = LoginHandler(self.mock_app, self.mock_request)
        handler.get_argument = MagicMock(side_effect=lambda k, d=None: "wrong_token" if k == "token" else "")
        
        with patch('aird.config.ACCESS_TOKEN', 'correct_token'), \
             patch('logging.warning') as mock_log, \
             patch.object(handler, 'render') as mock_render:
            
            handler.post()
            mock_render.assert_called_with("login.html", error="Invalid credentials. Try again.", settings=ANY, next_url=ANY)
            # Verify generic logging (security hardening removed detailed logging)
            assert mock_log.call_count >= 1
            assert any("Token authentication failed" in call[0][0] for call in mock_log.call_args_list)

    def test_post_token_length_mismatch_secure(self):
        """Verify that length mismatch does NOT log details (Side-channel protection)"""
        handler = LoginHandler(self.mock_app, self.mock_request)
        handler.get_argument = MagicMock(side_effect=lambda k, d=None: "short" if k == "token" else "")
        
        with patch('aird.config.ACCESS_TOKEN', 'much_longer_token'), \
             patch('logging.warning') as mock_log, \
             patch.object(handler, 'render') as mock_render:
            
            handler.post()
            # Verify NO logging of length specific mismatch
            args = [call[0][0] for call in mock_log.call_args_list]
            assert not any("Token length mismatch" in arg for arg in args)
            assert any("Token authentication failed" in arg for arg in args)

    def test_post_token_success(self):
        handler = LoginHandler(self.mock_app, self.mock_request)
        handler.get_argument = MagicMock(side_effect=lambda k, d=None: "valid_token" if k == "token" else "")
        
        with patch('aird.config.ACCESS_TOKEN', 'valid_token'), \
             patch.object(handler, 'set_secure_cookie') as mock_cookie, \
             patch.object(handler, 'redirect') as mock_redirect:
            
            handler.post()
            mock_redirect.assert_called()
            # Check cookies set
            cookies = {c[0][0]: c[0][1] for c in mock_cookie.call_args_list}
            assert cookies['user'] == 'token_authenticated'
            assert cookies['user_role'] == 'admin'

    def test_post_fallback_errors(self):
        handler = LoginHandler(self.mock_app, self.mock_request)
        # Case 1: No token, but username/password present (failed earlier check)
        handler.get_argument = MagicMock(side_effect=lambda k, d=None: "user" if k == "username" else "")
        with patch.object(handler, 'render') as mock_render:
            handler.post()
            mock_render.assert_called_with("login.html", error="Invalid username or password.", settings=ANY, next_url=ANY)

        # Case 2: Nothing provided
        handler.get_argument = MagicMock(return_value="")
        with patch.object(handler, 'render') as mock_render:
            handler.post()
            mock_render.assert_called_with("login.html", error="Username/password or token is required.", settings=ANY, next_url=ANY)

class TestAdminLoginHandlerExtended:
    def setup_method(self):
        self.mock_app = MagicMock()
        self.mock_request = MagicMock()
        self.mock_app.settings = {'cookie_secret': 'test_secret'}

    def test_get_already_admin(self):
        handler = AdminLoginHandler(self.mock_app, self.mock_request)
        with patch.object(handler, 'is_admin_user', return_value=True), \
             patch.object(handler, 'redirect') as mock_redirect:
            handler.get()
            mock_redirect.assert_called_with("/admin")

    def test_post_admin_token_success(self):
        handler = AdminLoginHandler(self.mock_app, self.mock_request)
        handler.get_argument = MagicMock(side_effect=lambda k, d=None: "admin_token" if k == "token" else "")
        
        with patch('aird.config.ADMIN_TOKEN', 'admin_token'), \
             patch.object(handler, 'set_secure_cookie') as mock_cookie, \
             patch.object(handler, 'redirect') as mock_redirect:
            
            handler.post()
            mock_redirect.assert_called_with("/admin")
            mock_cookie.assert_called_with("admin", "authenticated", httponly=True, secure=ANY, samesite="Strict")

    def test_post_admin_token_fail(self):
        handler = AdminLoginHandler(self.mock_app, self.mock_request)
        handler.get_argument = MagicMock(side_effect=lambda k, d=None: "wrong" if k == "token" else "")
        
        with patch('aird.config.ADMIN_TOKEN', 'admin_token'), \
             patch.object(handler, 'render') as mock_render:
            
            handler.post()
            mock_render.assert_called_with("admin_login.html", error="Invalid admin token.")

    def test_post_username_password_success(self):
        handler = AdminLoginHandler(self.mock_app, self.mock_request)
        handler.get_argument = MagicMock(side_effect=lambda k, d=None: "admin" if k == "username" else "pass")
        
        with patch('aird.handlers.auth_handlers.constants_module.DB_CONN', MagicMock()), \
             patch('aird.handlers.auth_handlers.authenticate_user', return_value={'role': 'admin'}), \
             patch.object(handler, 'set_secure_cookie') as mock_cookie, \
             patch.object(handler, 'redirect') as mock_redirect:
            
            handler.post()
            mock_redirect.assert_called_with("/admin")

    def test_post_username_password_not_admin(self):
        handler = AdminLoginHandler(self.mock_app, self.mock_request)
        handler.get_argument = MagicMock(side_effect=lambda k, d=None: "user" if k == "username" else "pass")
        
        with patch('aird.handlers.auth_handlers.constants_module.DB_CONN', MagicMock()), \
             patch('aird.handlers.auth_handlers.authenticate_user', return_value={'role': 'user'}), \
             patch.object(handler, 'render') as mock_render:
            
            handler.post()
            mock_render.assert_called_with("admin_login.html", error="Access denied. Admin privileges required.")

class TestLogoutHandler:
    def setup_method(self):
        self.mock_app = MagicMock()
        self.mock_request = MagicMock()
        self.mock_app.settings = {'cookie_secret': 'test_secret'}

    def test_get(self):
        handler = LogoutHandler(self.mock_app, self.mock_request)
        with patch.object(handler, 'clear_cookie') as mock_clear, \
             patch.object(handler, 'redirect') as mock_redirect:
            handler.get()
            mock_clear.assert_any_call("user")
            mock_clear.assert_any_call("admin")
            mock_redirect.assert_called_with("/login")

class TestProfileHandlerExtended:
    def setup_method(self):
        self.mock_app = MagicMock()
        self.mock_request = MagicMock()
        self.mock_app.settings = {'cookie_secret': 'test_secret'}

    def test_post_password_update_success(self):
        handler = ProfileHandler(self.mock_app, self.mock_request)
        handler.current_user = {'username': 'user', 'id': 1}
        handler.get_argument = MagicMock(side_effect=lambda k, d=None: "newpass" if "password" in k else "")
        
        with patch('aird.handlers.auth_handlers.constants_module.DB_CONN', MagicMock()), \
             patch('aird.handlers.auth_handlers.get_user_by_username', return_value={'id': 1}), \
             patch('aird.handlers.auth_handlers.update_user') as mock_update, \
             patch.object(handler, 'render') as mock_render:
            
            handler.post()
            mock_update.assert_called()
            mock_render.assert_called_with("profile.html", user=ANY, success="Password updated successfully", error=None, ldap_enabled=False)

    def test_post_password_mismatch(self):
        handler = ProfileHandler(self.mock_app, self.mock_request)
        handler.current_user = {'username': 'user', 'id': 1}
        def get_arg(k, d=None):
            if k == "new_password": return "pass1"
            if k == "confirm_password": return "pass2"
            return ""
        handler.get_argument = MagicMock(side_effect=get_arg)
        
        with patch('aird.handlers.auth_handlers.constants_module.DB_CONN', MagicMock()), \
             patch('aird.handlers.auth_handlers.get_user_by_username', return_value={'id': 1}), \
             patch.object(handler, 'render') as mock_render:
            
            handler.post()
            mock_render.assert_called_with("profile.html", user=ANY, error="Passwords do not match", success=None, ldap_enabled=False)

class TestBaseHandlerExtended:
    def setup_method(self):
        self.mock_app = MagicMock()
        self.mock_request = MagicMock()
        self.mock_app.settings = {'cookie_secret': 'test_secret'}

    def test_get_current_user_json_error(self):
        handler = BaseHandler(self.mock_app, self.mock_request)
        # Return invalid JSON
        handler.get_secure_cookie = MagicMock(return_value=b"{invalid_json}")
        
        user = handler.get_current_user()
        assert user is None

    def test_get_current_user_token_authenticated_bytes(self):
        handler = BaseHandler(self.mock_app, self.mock_request)
        handler.get_secure_cookie = MagicMock(return_value=b"token_authenticated")
        
        # Mock DB to fail or return None
        with patch('aird.constants.DB_CONN', MagicMock()), \
             patch('aird.db.get_user_by_username', side_effect=Exception("DB Error")):
            
            user = handler.get_current_user()
            assert user == {"username": "token_user", "role": "admin"}

    def test_write_error_exception(self):
        handler = BaseHandler(self.mock_app, self.mock_request)
        handler._reason = "Test Error"
        
        # Mock render to raise exception
        with patch.object(handler, 'render', side_effect=Exception("Render Error")), \
             patch.object(handler, 'write') as mock_write:
            
            handler.write_error(500)
            mock_write.assert_called_with("<html><body><h1>Error 500</h1><p>Test Error</p></body></html>")

    def test_on_finish_exception(self):
        handler = BaseHandler(self.mock_app, self.mock_request)
        
        # Mock super().on_finish to raise exception
        with patch('tornado.web.RequestHandler.on_finish', side_effect=Exception("Finish Error")):
            # Should not raise exception
            handler.on_finish()

    def test_is_admin_user_get_current_admin_exception(self):
        handler = BaseHandler(self.mock_app, self.mock_request)
        handler.get_current_admin = MagicMock(side_effect=Exception("Error"))
        handler.get_current_user = MagicMock(return_value=None)
        handler.get_secure_cookie = MagicMock(return_value=None)
        
        assert handler.is_admin_user() is False

    def test_is_admin_user_cookie_exception(self):
        handler = BaseHandler(self.mock_app, self.mock_request)
        handler.get_current_user = MagicMock(return_value=None)
        handler.get_secure_cookie = MagicMock(side_effect=Exception("Cookie Error"))
        
        assert handler.is_admin_user() is False

    def test_get_display_username_dict_token(self):
        handler = BaseHandler(self.mock_app, self.mock_request)
        handler.get_current_user = MagicMock(return_value={"username": "token_user", "role": "admin"})
        assert handler.get_display_username() == "Admin (Token)"

    def test_get_display_username_bytes_token(self):
        handler = BaseHandler(self.mock_app, self.mock_request)
        handler.get_current_user = MagicMock(return_value=b"token_authenticated")
        assert handler.get_display_username() == "Admin (Token)"

    def test_get_display_username_bytes_role_cookie(self):
        handler = BaseHandler(self.mock_app, self.mock_request)
        handler.get_current_user = MagicMock(return_value=b"someuser")
        handler.get_secure_cookie = MagicMock(return_value=b"admin")
        
        assert handler.get_display_username() == "someuser (Admin)"

    def test_get_display_username_dict_role_admin(self):
        handler = BaseHandler(self.mock_app, self.mock_request)
        handler.get_current_user = MagicMock(return_value={"username": "admin", "role": "admin"})
        assert handler.get_display_username() == "admin (Admin)"

    def test_get_display_username_dict_role_user(self):
        handler = BaseHandler(self.mock_app, self.mock_request)
        handler.get_current_user = MagicMock(return_value={"username": "user", "role": "user"})
        assert handler.get_display_username() == "user (User)"

    def test_get_display_username_dict_no_role(self):
        handler = BaseHandler(self.mock_app, self.mock_request)
        handler.get_current_user = MagicMock(return_value={"username": "guest"})
        assert handler.get_display_username() == "guest"

    def test_get_display_username_str_token(self):
        handler = BaseHandler(self.mock_app, self.mock_request)
        handler.get_current_user = MagicMock(return_value="token_authenticated")
        assert handler.get_display_username() == "Admin (Token)"

    def test_get_display_username_str_role_cookie_user(self):
        handler = BaseHandler(self.mock_app, self.mock_request)
        handler.get_current_user = MagicMock(return_value="user")
        handler.get_secure_cookie = MagicMock(return_value=b"user")
        assert handler.get_display_username() == "user (User)"
