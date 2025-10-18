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
def mock_root_dir(temp_dir):
    """Mock ROOT_DIR to use temporary directory"""
    with patch('aird.main.ROOT_DIR', temp_dir):
        yield temp_dir


@pytest.fixture
def sample_files(temp_dir):
    """Create sample files for testing"""
    files = {}
    
    # Create text file
    text_file = os.path.join(temp_dir, "sample.txt")
    with open(text_file, "w", encoding='utf-8') as f:
        f.write("Line 1: Hello world\nLine 2: Python testing\nLine 3: End of file\n")
    files['text'] = text_file
    
    # Create Python file
    py_file = os.path.join(temp_dir, "script.py")
    with open(py_file, "w", encoding='utf-8') as f:
        f.write("def hello():\n    print('Hello, world!')\n    return True\n")
    files['python'] = py_file
    
    # Create subdirectory with file
    subdir = os.path.join(temp_dir, "subdir")
    os.makedirs(subdir)
    sub_file = os.path.join(subdir, "nested.md")
    with open(sub_file, "w", encoding='utf-8') as f:
        f.write("# Nested File\n\nThis is a nested markdown file.\n")
    files['nested'] = sub_file
    
    # Create binary file
    bin_file = os.path.join(temp_dir, "binary.dat")
    with open(bin_file, "wb") as f:
        f.write(b'\x00\x01\x02\x03\x04\x05')
    files['binary'] = bin_file
    
    yield files


@pytest.fixture(autouse=True)
def reset_feature_flags():
    """Reset feature flags to default state before each test"""
    try:
        from aird.main import FEATURE_FLAGS
        original_flags = FEATURE_FLAGS.copy()
        yield
        FEATURE_FLAGS.clear()
        FEATURE_FLAGS.update(original_flags)
    except ImportError:
        # If aird.main can't be imported, just yield
        yield


@pytest.fixture
def mock_db_conn():
    """Mock database connection with initialized tables"""
    conn = sqlite3.connect(":memory:")
    try:
        from aird.main import _init_db
        _init_db(conn)
    except ImportError:
        # Create basic tables if aird.main can't be imported
        conn.execute('''CREATE TABLE IF NOT EXISTS feature_flags 
                        (key TEXT PRIMARY KEY, value INTEGER)''')
        conn.execute('''CREATE TABLE IF NOT EXISTS shares 
                        (id TEXT PRIMARY KEY, created TEXT, paths TEXT)''')
        conn.commit()
    
    with patch('aird.main.DB_CONN', conn):
        yield conn
    
    conn.close()


@pytest.fixture
def mock_tornado_app():
    """Mock Tornado application for handler testing"""
    app = MagicMock()
    app.settings = {
        'cookie_secret': 'test_secret_key_for_testing',
        'template_path': 'templates',
        'debug': False,
        'login_url': '/login'
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


@pytest.fixture
def disable_network():
    """Disable network access for tests"""
    with patch('socket.socket') as mock_socket:
        mock_socket.side_effect = OSError("Network access disabled in tests")
        yield


@pytest.fixture
def large_file(temp_dir):
    """Create a large file for testing memory-mapped operations"""
    file_path = os.path.join(temp_dir, "large_file.txt")
    with open(file_path, "w", encoding='utf-8') as f:
        for i in range(10000):
            f.write(f"Line {i}: This is a test line with some content for testing.\n")
    yield file_path