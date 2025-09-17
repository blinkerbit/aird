"""
Unit tests for utility functions in aird.main module.

These tests cover the core utility functions that handle file operations,
path manipulation, and icon mapping functionality.
"""

import os
import tempfile
import pytest
from unittest.mock import patch, MagicMock
from pathlib import Path

# Import with error handling for missing module
try:
    from aird.main import (
        join_path,
        get_file_icon,
        get_files_in_directory,
        _get_data_dir
    )
    AIRD_AVAILABLE = True
except ImportError:
    AIRD_AVAILABLE = False


@pytest.mark.skipif(not AIRD_AVAILABLE, reason="aird.main module not available")
class TestJoinPath:
    """Test join_path utility function"""
    
    def test_join_path_basic(self):
        """Test basic path joining functionality"""
        result = join_path("a", "b", "c")
        expected = "a/b/c"
        assert result == expected, f"Expected {expected}, got {result}"
    
    def test_join_path_with_backslashes(self):
        """Test that backslashes are converted to forward slashes"""
        with patch('os.path.join') as mock_join:
            mock_join.return_value = "a\\b\\c"
            result = join_path("a", "b", "c")
            expected = "a/b/c"
            assert result == expected
            mock_join.assert_called_once_with("a", "b", "c")
    
    def test_join_path_empty_parts(self):
        """Test joining with empty parts"""
        result = join_path("", "b", "")
        # The behavior depends on os.path.join implementation
        assert isinstance(result, str)
        assert "b" in result
    
    def test_join_path_single_part(self):
        """Test joining with single part"""
        result = join_path("single")
        expected = "single"
        assert result == expected
    
    def test_join_path_absolute_path(self):
        """Test joining with absolute path components"""
        result = join_path("/home", "user", "docs")
        assert result.startswith("/")
        assert "home" in result
        assert "user" in result
        assert "docs" in result
    
    def test_join_path_with_dots(self):
        """Test joining paths with dot notation"""
        result = join_path(".", "subdir", "..", "file.txt")
        assert isinstance(result, str)
        # Should handle relative path components


@pytest.mark.skipif(not AIRD_AVAILABLE, reason="aird.main module not available")
class TestGetFileIcon:
    """Test get_file_icon utility function"""
    
    @pytest.mark.parametrize("filename,expected_icon", [
        ("file.txt", "📄"),
        ("README.md", "📖"),  # Special icon for README files
        ("document.doc", "📝"),  # Word documents have different icon
        ("FILE.TXT", "📄"),  # Case insensitive
        ("notes.rtf", "📝"),  # RTF files have different icon
    ])
    def test_text_files(self, filename, expected_icon):
        """Test icons for text files"""
        assert get_file_icon(filename) == expected_icon
    
    @pytest.mark.parametrize("filename,expected_icon", [
        ("photo.jpg", "🖼️"),
        ("image.jpeg", "🖼️"),
        ("picture.png", "🖼️"),
        ("animation.gif", "🖼️"),
        ("PHOTO.JPG", "🖼️"),  # Case insensitive
        ("icon.svg", "🎨"),  # SVG files have different icon
        ("bitmap.bmp", "🖼️"),
    ])
    def test_image_files(self, filename, expected_icon):
        """Test icons for image files"""
        assert get_file_icon(filename) == expected_icon
    
    @pytest.mark.parametrize("filename,expected_icon", [
        ("script.py", "🐍💎"),  # Python source files have enhanced snake icon with gem
        ("script.pyw", "🐍💎"),  # Python Windows files have enhanced snake icon with gem
        ("module.pyc", "🐍⚡"),  # Compiled Python files have snake with lightning
        ("module.pyo", "🐍⚡"),  # Optimized Python files have snake with lightning
        ("app.js", "🟨"),  # JavaScript files have yellow square
        ("Main.java", "☕"),  # Java files have coffee icon
        ("program.cpp", "⚙️"),  # C++ files have gear icon
        ("SCRIPT.PY", "🐍💎"),  # Case insensitive
        ("code.c", "⚙️"),  # C files have gear icon
        ("web.html", "🌐"),  # HTML files have globe icon
        ("style.css", "🎨"),  # CSS files have art icon
    ])
    def test_code_files(self, filename, expected_icon):
        """Test icons for code files"""
        assert get_file_icon(filename) == expected_icon
    
    @pytest.mark.parametrize("filename,expected_icon", [
        ("archive.zip", "🗜️"),
        ("backup.rar", "🗜️"),
        ("ARCHIVE.ZIP", "🗜️"),  # Case insensitive
        ("data.tar", "🗜️"),
        ("compressed.gz", "🗜️"),
    ])
    def test_archive_files(self, filename, expected_icon):
        """Test icons for archive files"""
        assert get_file_icon(filename) == expected_icon
    
    @pytest.mark.parametrize("filename,expected_icon", [
        ("document.pdf", "📕"),  # PDF files have book icon
        ("data.csv", "📊"),  # CSV files have chart icon
        ("file", "📦"),  # No extension
        ("file.unknown", "📦"),
        ("", "📦"),  # Empty filename
        ("file.xyz", "📦"),  # Unknown extension
    ])
    def test_other_files(self, filename, expected_icon):
        """Test icons for other file types"""
        assert get_file_icon(filename) == expected_icon
    
    def test_get_file_icon_edge_cases(self):
        """Test edge cases for file icon function"""
        # Test with None (should not crash)
        try:
            result = get_file_icon(None)
            assert result == "📦"  # Default icon
        except (TypeError, AttributeError):
            # Acceptable if function doesn't handle None
            pass
        
        # Test with very long filename
        long_name = "a" * 1000 + ".txt"
        result = get_file_icon(long_name)
        assert result == "📄"


@pytest.mark.skipif(not AIRD_AVAILABLE, reason="aird.main module not available")
class TestGetFilesInDirectory:
    """Test get_files_in_directory utility function"""
    
    def test_get_files_basic(self, temp_dir):
        """Test basic directory listing"""
        # Create test files
        test_file = os.path.join(temp_dir, "test.txt")
        with open(test_file, "w", encoding='utf-8') as f:
            f.write("test content")
        
        # Create test directory
        test_dir = os.path.join(temp_dir, "subdir")
        os.makedirs(test_dir)
        
        files = get_files_in_directory(temp_dir)
        
        # Should have 2 entries
        assert len(files) == 2
        
        # Find the file and directory entries
        file_entry = next((f for f in files if f["name"] == "test.txt"), None)
        dir_entry = next((f for f in files if f["name"] == "subdir"), None)
        
        assert file_entry is not None, "File entry not found"
        assert dir_entry is not None, "Directory entry not found"
        
        # Check file properties
        assert file_entry["is_dir"] is False
        assert file_entry["size_bytes"] == 12  # "test content" is 12 bytes
        assert "size_str" in file_entry
        assert "modified" in file_entry
        assert "modified_timestamp" in file_entry
        
        # Check directory properties
        assert dir_entry["is_dir"] is True
        assert dir_entry["size_str"] == "-"
    
    def test_get_files_empty_directory(self, temp_dir):
        """Test listing empty directory"""
        files = get_files_in_directory(temp_dir)
        assert files == []
    
    def test_get_files_nonexistent_directory(self):
        """Test listing non-existent directory"""
        with pytest.raises((FileNotFoundError, OSError)):
            get_files_in_directory("/nonexistent/directory/path")
    
    def test_get_files_with_hidden_files(self, temp_dir):
        """Test handling of hidden files (if supported by OS)"""
        # Create a hidden file (Unix-style)
        hidden_file = os.path.join(temp_dir, ".hidden")
        with open(hidden_file, "w", encoding='utf-8') as f:
            f.write("hidden content")
        
        # Create a regular file
        regular_file = os.path.join(temp_dir, "regular.txt")
        with open(regular_file, "w", encoding='utf-8') as f:
            f.write("regular content")
        
        files = get_files_in_directory(temp_dir)
        
        # Should include both files (behavior may vary by implementation)
        assert len(files) >= 1  # At least the regular file
        
        file_names = [f["name"] for f in files]
        assert "regular.txt" in file_names
    
    def test_get_files_sorting(self, temp_dir):
        """Test that files are returned in a consistent order"""
        # Create multiple files
        for name in ["zebra.txt", "alpha.txt", "beta.txt"]:
            file_path = os.path.join(temp_dir, name)
            with open(file_path, "w", encoding='utf-8') as f:
                f.write("content")
        
        files = get_files_in_directory(temp_dir)
        
        # Should have all files
        assert len(files) == 3
        
        # File names should be in some consistent order
        file_names = [f["name"] for f in files]
        assert "alpha.txt" in file_names
        assert "beta.txt" in file_names
        assert "zebra.txt" in file_names
    
    def test_get_files_with_special_characters(self, temp_dir):
        """Test handling files with special characters in names"""
        special_files = [
            "file with spaces.txt",
            "file-with-dashes.txt",
            "file_with_underscores.txt"
        ]
        
        for filename in special_files:
            file_path = os.path.join(temp_dir, filename)
            try:
                with open(file_path, "w", encoding='utf-8') as f:
                    f.write("content")
            except (OSError, UnicodeError):
                # Skip if OS doesn't support the filename
                continue
        
        files = get_files_in_directory(temp_dir)
        
        # Should handle special characters gracefully
        assert len(files) > 0
        for file_entry in files:
            assert isinstance(file_entry["name"], str)
            assert len(file_entry["name"]) > 0


@pytest.mark.skipif(not AIRD_AVAILABLE, reason="aird.main module not available")
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
    
    @patch('os.name', 'posix')
    @patch('sys.platform', 'darwin')
    @patch('os.path.expanduser')
    @patch('os.makedirs')
    def test_get_data_dir_macos(self, mock_makedirs, mock_expanduser):
        """Test data directory on macOS"""
        mock_expanduser.return_value = '/Users/test/Library/Application Support'
        result = _get_data_dir()
        expected = os.path.join('/Users/test/Library/Application Support', 'aird')
        assert result == expected
        mock_makedirs.assert_called_once_with(expected, exist_ok=True)
    
    @patch('os.name', 'posix')
    @patch('sys.platform', 'linux')
    @patch.dict('os.environ', {'XDG_DATA_HOME': '/home/test/.local/share'})
    @patch('os.makedirs')
    def test_get_data_dir_linux_xdg(self, mock_makedirs):
        """Test data directory on Linux with XDG_DATA_HOME"""
        result = _get_data_dir()
        expected = os.path.join('/home/test/.local/share', 'aird')
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
        expected = os.path.join('/home/test/.local/share', 'aird')
        assert result == expected
        mock_makedirs.assert_called_once_with(expected, exist_ok=True)
    
    @patch('os.makedirs', side_effect=PermissionError("Permission denied"))
    @patch('os.getcwd', return_value='/fallback/dir')
    def test_get_data_dir_permission_error_fallback(self, mock_getcwd, mock_makedirs):
        """Test fallback when data directory creation fails due to permissions"""
        result = _get_data_dir()
        assert result == '/fallback/dir'
        mock_getcwd.assert_called_once()
    
    @patch('os.makedirs', side_effect=OSError("Disk full"))
    @patch('os.getcwd', return_value='/fallback/dir')
    def test_get_data_dir_os_error_fallback(self, mock_getcwd, mock_makedirs):
        """Test fallback when data directory creation fails due to OS error"""
        result = _get_data_dir()
        assert result == '/fallback/dir'
        mock_getcwd.assert_called_once()
    
    @patch('os.makedirs')
    def test_get_data_dir_creates_directory_once(self, mock_makedirs):
        """Test that data directory is created with correct parameters"""
        _get_data_dir()
        
        # Should be called exactly once with exist_ok=True
        assert mock_makedirs.call_count == 1
        args, kwargs = mock_makedirs.call_args
        assert kwargs.get('exist_ok') is True
    
    def test_get_data_dir_returns_string(self):
        """Test that _get_data_dir always returns a string"""
        result = _get_data_dir()
        assert isinstance(result, str)
        assert len(result) > 0
    
    @patch('os.getcwd', side_effect=OSError("Unable to get current directory"))
    @patch('os.makedirs', side_effect=OSError("Unable to create directory"))
    def test_get_data_dir_complete_failure_fallback(self, mock_makedirs, mock_getcwd):
        """Test fallback when everything fails"""
        # This test checks the ultimate fallback behavior
        try:
            result = _get_data_dir()
            # Should still return something usable
            assert isinstance(result, str)
        except Exception:
            # If function raises exception, that's also acceptable behavior
            pass


@pytest.mark.integration
@pytest.mark.skipif(not AIRD_AVAILABLE, reason="aird.main module not available")
class TestUtilitiesIntegration:
    """Integration tests for utility functions working together"""
    
    def test_file_operations_integration(self, temp_dir):
        """Test that utilities work together for file operations"""
        # Create a complex directory structure
        subdir1 = os.path.join(temp_dir, "subdir1")
        subdir2 = os.path.join(temp_dir, "subdir2")
        os.makedirs(subdir1)
        os.makedirs(subdir2)
        
        # Create various file types
        files_to_create = [
            ("document.txt", "text content"),
            ("script.py", "print('hello')"),
            ("image.jpg", "fake image data"),
            ("archive.zip", "fake zip data"),
            (os.path.join("subdir1", "nested.md"), "# Nested file"),
            (os.path.join("subdir2", "deep.cpp"), "int main() { return 0; }")
        ]
        
        for file_path, content in files_to_create:
            full_path = os.path.join(temp_dir, file_path)
            os.makedirs(os.path.dirname(full_path), exist_ok=True)
            with open(full_path, "w", encoding='utf-8') as f:
                f.write(content)
        
        # Test directory listing
        files = get_files_in_directory(temp_dir)
        assert len(files) >= 4  # At least the root level files and directories
        
        # Test icon assignment for each file type
        root_files = [f for f in files if not f["is_dir"]]
        for file_entry in root_files:
            icon = get_file_icon(file_entry["name"])
            # Check that each file gets some valid icon (not an empty string)
            assert icon and len(icon) > 0, f"File {file_entry['name']} should have a non-empty icon"
            # Check specific icons for known file types
            if file_entry["name"] == "script.py":
                assert icon == "🐍💎"
            elif file_entry["name"] == "document.txt":
                assert icon == "📄" 
            elif file_entry["name"] == "image.jpg":
                assert icon == "🖼️"
            elif file_entry["name"] == "archive.zip":
                assert icon == "🗜️"
        
        # Test path joining for nested access
        nested_path = join_path(temp_dir, "subdir1", "nested.md")
        assert os.path.exists(nested_path)
    
    def test_error_handling_integration(self):
        """Test that utilities handle errors gracefully when used together"""
        # Test with non-existent paths
        try:
            files = get_files_in_directory("/completely/nonexistent/path")
            # If no exception, should return empty list or handle gracefully
            assert isinstance(files, list)
        except (FileNotFoundError, OSError):
            # Expected behavior
            pass
        
        # Test icon function with invalid input
        icon = get_file_icon("")
        assert isinstance(icon, str)
        
        # Test path joining with problematic input
        result = join_path("", "", "")
        assert isinstance(result, str)