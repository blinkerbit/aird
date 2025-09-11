"""
Unit tests for utility functions in aird.main module.
"""

import os
import tempfile
import pytest
from unittest.mock import patch, MagicMock
from aird.main import (
    join_path,
    get_file_icon,
    get_files_in_directory,
    _get_data_dir
)


class TestJoinPath:
    """Test join_path utility function"""
    
    def test_join_path_basic(self):
        """Test basic path joining"""
        result = join_path("a", "b", "c")
        expected = "a/b/c"
        assert result == expected
    
    def test_join_path_with_backslashes(self):
        """Test that backslashes are converted to forward slashes"""
        with patch('os.path.join') as mock_join:
            mock_join.return_value = "a\\b\\c"
            result = join_path("a", "b", "c")
            expected = "a/b/c"
            assert result == expected
    
    def test_join_path_empty_parts(self):
        """Test joining with empty parts"""
        result = join_path("", "b", "")
        expected = "/b/"
        assert result == expected
    
    def test_join_path_single_part(self):
        """Test joining with single part"""
        result = join_path("single")
        expected = "single"
        assert result == expected


class TestGetFileIcon:
    """Test get_file_icon utility function"""
    
    def test_text_files(self):
        """Test icons for text files"""
        assert get_file_icon("file.txt") == "📄"
        assert get_file_icon("README.md") == "📄"
        assert get_file_icon("FILE.TXT") == "📄"  # Case insensitive
    
    def test_image_files(self):
        """Test icons for image files"""
        assert get_file_icon("photo.jpg") == "🖼️"
        assert get_file_icon("image.jpeg") == "🖼️"
        assert get_file_icon("picture.png") == "🖼️"
        assert get_file_icon("animation.gif") == "🖼️"
        assert get_file_icon("PHOTO.JPG") == "🖼️"  # Case insensitive
    
    def test_code_files(self):
        """Test icons for code files"""
        assert get_file_icon("script.py") == "💻"
        assert get_file_icon("app.js") == "💻"
        assert get_file_icon("Main.java") == "💻"
        assert get_file_icon("program.cpp") == "💻"
        assert get_file_icon("SCRIPT.PY") == "💻"  # Case insensitive
    
    def test_archive_files(self):
        """Test icons for archive files"""
        assert get_file_icon("archive.zip") == "🗜️"
        assert get_file_icon("backup.rar") == "🗜️"
        assert get_file_icon("ARCHIVE.ZIP") == "🗜️"  # Case insensitive
    
    def test_other_files(self):
        """Test icons for other file types"""
        assert get_file_icon("document.pdf") == "📦"
        assert get_file_icon("data.csv") == "📦"
        assert get_file_icon("file") == "📦"  # No extension
        assert get_file_icon("file.unknown") == "📦"


class TestGetFilesInDirectory:
    """Test get_files_in_directory utility function"""
    
    def test_get_files_basic(self):
        """Test basic directory listing"""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create test files
            test_file = os.path.join(tmpdir, "test.txt")
            with open(test_file, "w") as f:
                f.write("test content")
            
            # Create test directory
            test_dir = os.path.join(tmpdir, "subdir")
            os.makedirs(test_dir)
            
            files = get_files_in_directory(tmpdir)
            
            # Should have 2 entries
            assert len(files) == 2
            
            # Find the file and directory entries
            file_entry = next((f for f in files if f["name"] == "test.txt"), None)
            dir_entry = next((f for f in files if f["name"] == "subdir"), None)
            
            assert file_entry is not None
            assert dir_entry is not None
            
            # Check file properties
            assert file_entry["is_dir"] is False
            assert file_entry["size_bytes"] == 12  # "test content" is 12 bytes
            assert "KB" in file_entry["size_str"]
            assert "modified" in file_entry
            assert "modified_timestamp" in file_entry
            
            # Check directory properties
            assert dir_entry["is_dir"] is True
            assert dir_entry["size_str"] == "-"
    
    def test_get_files_empty_directory(self):
        """Test listing empty directory"""
        with tempfile.TemporaryDirectory() as tmpdir:
            files = get_files_in_directory(tmpdir)
            assert files == []
    
    def test_get_files_nonexistent_directory(self):
        """Test listing non-existent directory"""
        with pytest.raises(FileNotFoundError):
            get_files_in_directory("/nonexistent/directory")


class TestGetDataDir:
    """Test _get_data_dir utility function"""
    
    @patch('os.name', 'nt')
    @patch.dict('os.environ', {'LOCALAPPDATA': 'C:\\Users\\test\\AppData\\Local'})
    @patch('os.makedirs')
    def test_get_data_dir_windows(self, mock_makedirs):
        """Test data directory on Windows"""
        result = _get_data_dir()
        expected = os.path.join('C:\\Users\\test\\AppData\\Local', 'aird')
        assert result == expected
        mock_makedirs.assert_called_once_with(expected, exist_ok=True)
    
    @patch('sys.platform', 'darwin')
    @patch('os.path.expanduser')
    @patch('os.makedirs')
    def test_get_data_dir_macos(self, mock_makedirs, mock_expanduser):
        """Test data directory on macOS"""
        mock_expanduser.return_value = '/Users/test/Library/Application Support'
        result = _get_data_dir()
        expected = '/Users/test/Library/Application Support/aird'
        assert result == expected
        mock_makedirs.assert_called_once_with(expected, exist_ok=True)
    
    @patch('os.name', 'posix')
    @patch('sys.platform', 'linux')
    @patch.dict('os.environ', {'XDG_DATA_HOME': '/home/test/.local/share'})
    @patch('os.makedirs')
    def test_get_data_dir_linux_xdg(self, mock_makedirs):
        """Test data directory on Linux with XDG_DATA_HOME"""
        result = _get_data_dir()
        expected = '/home/test/.local/share/aird'
        assert result == expected
        mock_makedirs.assert_called_once_with(expected, exist_ok=True)
    
    @patch('os.name', 'posix')
    @patch('sys.platform', 'linux')
    @patch.dict('os.environ', {}, clear=True)
    @patch('os.path.expanduser')
    @patch('os.makedirs')
    def test_get_data_dir_linux_fallback(self, mock_makedirs, mock_expanduser):
        """Test data directory on Linux without XDG_DATA_HOME"""
        mock_expanduser.return_value = '/home/test/.local/share'
        result = _get_data_dir()
        expected = '/home/test/.local/share/aird'
        assert result == expected
        mock_makedirs.assert_called_once_with(expected, exist_ok=True)
    
    @patch('os.makedirs', side_effect=Exception("Permission denied"))
    @patch('os.getcwd', return_value='/fallback/dir')
    def test_get_data_dir_exception_fallback(self, mock_getcwd, mock_makedirs):
        """Test fallback when data directory creation fails"""
        result = _get_data_dir()
        assert result == '/fallback/dir'
        mock_getcwd.assert_called_once()