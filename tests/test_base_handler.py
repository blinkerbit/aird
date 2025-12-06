
import pytest
from unittest.mock import patch, MagicMock
from aird.handlers.base_handler import BaseHandler
import json

from tests.handler_helpers import authenticate, patch_db_conn, prepare_handler

class TestBaseHandler:
    def setup_method(self):
        self.mock_app = MagicMock()
        self.mock_request = MagicMock()
        self.mock_app.settings = {'cookie_secret': 'test_secret'}
        self.mock_request.headers = {}
        self.mock_request.protocol = "http"

    def test_get_current_user_from_cookie(self):
        handler = prepare_handler(BaseHandler(self.mock_app, self.mock_request))
        user_data = {'username': 'testuser', 'role': 'user'}
        
        db_conn = MagicMock()
        with patch.object(handler, 'get_secure_cookie', return_value=json.dumps(user_data).encode('utf-8')), \
             patch_db_conn(db_conn, modules=['aird.handlers.base_handler']), \
             patch('aird.db.get_user_by_username', return_value=user_data):
            
            user = handler.get_current_user()
            assert user['username'] == 'testuser'

    def test_get_current_user_from_token_header(self):
        handler = prepare_handler(BaseHandler(self.mock_app, self.mock_request))
        handler.request.headers = {'Authorization': 'Bearer valid_token'}
        
        with patch.object(handler, 'get_secure_cookie', return_value=None), \
             patch('aird.handlers.base_handler.config_module.ACCESS_TOKEN', 'valid_token'):
            
            user = handler.get_current_user()
            assert user['username'] == 'token_user'
            assert user['role'] == 'admin'

    def test_is_admin_user_true(self):
        handler = prepare_handler(BaseHandler(self.mock_app, self.mock_request))
        authenticate(handler, role='admin')
        assert handler.is_admin_user() is True

    def test_is_admin_user_false(self):
        handler = prepare_handler(BaseHandler(self.mock_app, self.mock_request))
        authenticate(handler, role='user')
        with patch.object(handler, 'get_secure_cookie', return_value=None):
            assert handler.is_admin_user() is False

    def test_get_display_username(self):
        handler = prepare_handler(BaseHandler(self.mock_app, self.mock_request))
        
        # Test regular user
        with patch.object(handler, 'get_current_user', return_value={'username': 'user', 'role': 'user'}):
            assert handler.get_display_username() == 'user (User)'
        
        # Test admin user
        with patch.object(handler, 'get_current_user', return_value={'username': 'admin', 'role': 'admin'}):
            assert handler.get_display_username() == 'admin (Admin)'
        
        # Test token user
        with patch.object(handler, 'get_current_user', return_value={'username': 'token_user', 'role': 'admin'}):
            assert handler.get_display_username() == 'Admin (Token)'
