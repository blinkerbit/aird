"""
Pytest configuration and shared fixtures for aird tests.
"""

import pytest
import tempfile
import shutil
import os
import sqlite3
from unittest.mock import patch, MagicMock
import asyncio


@pytest.fixture
def temp_dir():
    """Create a temporary directory for testing"""
    temp_path = tempfile.mkdtemp()
    yield temp_path
    shutil.rmtree(temp_path, ignore_errors=True)


@pytest.fixture
def test_db():
    """Create a temporary SQLite database for testing"""
    conn = sqlite3.connect(":memory:")
    yield conn
    conn.close()


@pytest.fixture
def mock_db_conn():
    """Mock database connection with initialized tables"""
    conn = sqlite3.connect(":memory:")
    try:
        from aird.db import init_db
        init_db(conn)
    except ImportError:
        # Create basic tables if aird.db can't be imported
        conn.execute('''CREATE TABLE IF NOT EXISTS feature_flags 
                        (key TEXT PRIMARY KEY, value INTEGER)''')
        conn.execute('''CREATE TABLE IF NOT EXISTS shares 
                        (id TEXT PRIMARY KEY, created TEXT, paths TEXT)''')
        conn.execute('''CREATE TABLE IF NOT EXISTS users 
                        (id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT UNIQUE, 
                         password_hash TEXT, role TEXT, active INTEGER DEFAULT 1, 
                         created_at TEXT, last_login TEXT)''')
        conn.execute('''CREATE TABLE IF NOT EXISTS websocket_config 
                        (key TEXT PRIMARY KEY, value TEXT)''')
        conn.commit()
    
    with patch('aird.constants.DB_CONN', conn), \
         patch('aird.handlers.admin_handlers.constants_module.DB_CONN', conn), \
         patch('aird.handlers.admin_handlers.DB_CONN', conn, create=True):
        yield conn
    
    conn.close()


@pytest.fixture(autouse=True)
def reset_feature_flags():
    """Reset feature flags to default state before each test"""
    pass


@pytest.fixture
def mock_tornado_app():
    """Mock Tornado application for handler testing"""
    app = MagicMock()
    app.settings = {
        'cookie_secret': 'test_secret_key_for_testing',
        'template_path': 'templates',
        'debug': False,
        'login_url': '/login',
        'ldap_server': None  # LDAP disabled by default
    }
    return app


@pytest.fixture
def mock_tornado_request():
    """Mock Tornado request for handler testing"""
    request = MagicMock()
    request.method = "GET"
    request.path = "/"
    request.body = b""
    request.headers = {}
    request.arguments = {}
    request.files = {}
    request.host = "localhost:8000"
    request.protocol = "http"
    request.connection = MagicMock()
    request.connection.context = MagicMock()
    return request


@pytest.fixture
def event_loop():
    """Create an instance of the default event loop for the test session."""
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


# Pytest configuration
def pytest_configure(config):
    """Configure pytest with custom markers"""
    config.addinivalue_line("markers", "unit: Unit tests")
    config.addinivalue_line("markers", "integration: Integration tests")
    config.addinivalue_line("markers", "slow: Slow tests")
    config.addinivalue_line("markers", "database: Database tests")
    config.addinivalue_line("markers", "security: Security tests")
    config.addinivalue_line("markers", "asyncio: Async tests")


@pytest.fixture(autouse=True)
def reset_login_rate_limit():
    """Reset the global login rate limit counter before each test."""
    try:
        from aird.handlers.auth_handlers import _LOGIN_ATTEMPTS
        _LOGIN_ATTEMPTS.clear()
    except ImportError:
        pass
    yield

