"""
Pytest configuration and shared fixtures for aird tests.
"""

import pytest
import tempfile
import shutil
import os
from unittest.mock import patch
import sqlite3


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
    with open(text_file, "w") as f:
        f.write("Line 1: Hello world\nLine 2: Python testing\nLine 3: End of file\n")
    files['text'] = text_file
    
    # Create Python file
    py_file = os.path.join(temp_dir, "script.py")
    with open(py_file, "w") as f:
        f.write("def hello():\n    print('Hello, world!')\n    return True\n")
    files['python'] = py_file
    
    # Create subdirectory with file
    subdir = os.path.join(temp_dir, "subdir")
    os.makedirs(subdir)
    sub_file = os.path.join(subdir, "nested.md")
    with open(sub_file, "w") as f:
        f.write("# Nested File\n\nThis is a nested markdown file.\n")
    files['nested'] = sub_file
    
    yield files


@pytest.fixture(autouse=True)
def reset_feature_flags():
    """Reset feature flags to default state before each test"""
    from aird.main import FEATURE_FLAGS
    original_flags = FEATURE_FLAGS.copy()
    yield
    FEATURE_FLAGS.clear()
    FEATURE_FLAGS.update(original_flags)


@pytest.fixture
def mock_db_conn():
    """Mock database connection"""
    conn = sqlite3.connect(":memory:")
    from aird.main import _init_db
    _init_db(conn)
    
    with patch('aird.main.DB_CONN', conn):
        yield conn
    
    conn.close()


# Configure pytest-asyncio
pytest_plugins = ['pytest_asyncio']