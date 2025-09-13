#!/usr/bin/env python3
"""
Tests for additional utility functions.

This module tests utility functions that may not be fully covered
in other test files.
"""

import pytest
import os
import tempfile
from unittest.mock import MagicMock, patch

# Try to import from aird.main, skip tests if not available
try:
    from aird.main import (
        join_path, is_video_file, is_audio_file, get_files_in_directory,
        print_banner, make_app
    )
    AIRD_AVAILABLE = True
except ImportError:
    AIRD_AVAILABLE = False


@pytest.mark.skipif(not AIRD_AVAILABLE, reason="aird.main module not available")
class TestUtilityFunctions:
    """Test various utility functions"""

    def test_join_path_single_part(self):
        """Test join_path with single part"""
        result = join_path("test")
        assert result == "test"

    def test_join_path_multiple_parts(self):
        """Test join_path with multiple parts"""
        result = join_path("path", "to", "file.txt")
        assert result == "path/to/file.txt"

    def test_join_path_with_backslashes(self):
        """Test join_path converts backslashes to forward slashes"""
        with patch('os.path.join', return_value="path\\to\\file.txt"):
            result = join_path("path", "to", "file.txt")
            assert result == "path/to/file.txt"

    def test_join_path_empty_parts(self):
        """Test join_path with empty parts"""
        result = join_path("", "path", "", "file.txt")
        # Behavior depends on os.path.join, but should still normalize slashes
        assert "/" in result or result == "file.txt"

    @pytest.mark.parametrize("filename,expected", [
        ("video.mp4", True),
        ("movie.avi", True),
        ("clip.mkv", True),
        ("animation.gif", False),  # GIF is not considered video in this implementation
        ("video.MP4", True),  # Case insensitive
        ("document.txt", False),
        ("audio.mp3", False),
        ("image.jpg", False),
        ("", False),
        ("video", False),  # No extension
    ])
    def test_is_video_file(self, filename, expected):
        """Test video file detection"""
        assert is_video_file(filename) == expected

    @pytest.mark.parametrize("filename,expected", [
        ("song.mp3", True),
        ("music.wav", True),
        ("audio.flac", True),
        ("sound.aac", True),
        ("audio.MP3", True),  # Case insensitive
        ("video.mp4", False),
        ("document.txt", False),
        ("image.jpg", False),
        ("", False),
        ("audio", False),  # No extension
    ])
    def test_is_audio_file(self, filename, expected):
        """Test audio file detection"""
        assert is_audio_file(filename) == expected

    def test_get_files_in_directory_current_dir(self):
        """Test getting files in current directory"""
        # Use a temporary directory to control the test environment
        with tempfile.TemporaryDirectory() as temp_dir:
            # Create test files
            test_files = [
                'file1.txt',
                'file2.py'
            ]
            
            for filename in test_files:
                with open(os.path.join(temp_dir, filename), 'w') as f:
                    f.write("test content")
            
            # Create subdirectory
            subdir = os.path.join(temp_dir, 'dir1')
            os.makedirs(subdir)
            
            files = get_files_in_directory(temp_dir)
            
            # Should include all files plus subdirectory
            assert len(files) == 3
            # Check that files are properly categorized
            file_names = [f['name'] for f in files]
            assert 'file1.txt' in file_names
            assert 'dir1' in file_names
            assert 'file2.py' in file_names

    def test_get_files_in_directory_with_exception(self):
        """Test get_files_in_directory handles exceptions gracefully"""
        with patch('os.scandir', side_effect=PermissionError("Access denied")):
            try:
                files = get_files_in_directory(".")
                assert files == []
            except PermissionError:
                # This is also acceptable behavior
                pass

    def test_get_files_in_directory_nonexistent(self):
        """Test get_files_in_directory with non-existent directory"""
        with patch('os.scandir', side_effect=FileNotFoundError("Directory not found")):
            try:
                files = get_files_in_directory("/nonexistent")
                assert files == []
            except FileNotFoundError:
                # This is also acceptable behavior
                pass

    def test_print_banner(self):
        """Test print_banner function"""
        with patch('builtins.print') as mock_print:
            print_banner()
            
            # Should have called print at least once
            assert mock_print.called
            # Check that ASCII art was printed
            printed_text = ''.join(call.args[0] for call in mock_print.call_args_list)
            assert '█' in printed_text or 'A' in printed_text

    def test_make_app_basic(self):
        """Test make_app with basic settings"""
        settings = {
            'cookie_secret': 'test_secret',
            'template_path': 'templates'
        }
        
        with patch('os.path.join', return_value='templates'), \
             patch('os.path.dirname', return_value='.'):
            app = make_app(settings)
            
            assert app is not None
            assert hasattr(app, 'settings')
            assert hasattr(app, 'default_router')
            assert 'template_path' in app.settings

    def test_make_app_with_ldap(self):
        """Test make_app with LDAP enabled"""
        settings = {
            'cookie_secret': 'test_secret',
            'template_path': 'templates'
        }
        
        with patch('os.path.join', return_value='templates'), \
             patch('os.path.dirname', return_value='.'):
            app = make_app(
                settings, 
                ldap_enabled=True,
                ldap_server='ldap://test.com',
                ldap_base_dn='dc=test,dc=com'
            )
            
            assert app is not None
            assert app.settings['ldap_server'] == 'ldap://test.com'
            assert app.settings['ldap_base_dn'] == 'dc=test,dc=com'

    def test_make_app_sets_default_limits(self):
        """Test make_app sets appropriate default limits"""
        settings = {}
        
        with patch('os.path.join', return_value='templates'), \
             patch('os.path.dirname', return_value='.'):
            app = make_app(settings)
            
            # Should set template_path
            assert 'template_path' in app.settings
            # Should set body size limits
            assert 'max_body_size' in app.settings
            assert 'max_buffer_size' in app.settings

    def test_make_app_preserves_existing_limits(self):
        """Test make_app preserves existing limit settings"""
        settings = {
            'max_body_size': 123456,
            'max_buffer_size': 654321
        }
        
        with patch('os.path.join', return_value='templates'), \
             patch('os.path.dirname', return_value='.'):
            app = make_app(settings)
            
            # Should preserve existing values
            assert app.settings['max_body_size'] == 123456
            assert app.settings['max_buffer_size'] == 654321


@pytest.mark.skipif(not AIRD_AVAILABLE, reason="aird.main module not available")
class TestFileOperationsIntegration:
    """Test file operations integration scenarios"""

    def test_file_listing_with_mixed_types(self):
        """Test file listing with various file types"""
        with tempfile.TemporaryDirectory() as temp_dir:
            # Create test files
            test_files = [
                'document.txt',
                'video.mp4',
                'audio.mp3',
                'image.jpg',
                'script.py',
                'data.json'
            ]
            
            for filename in test_files:
                with open(os.path.join(temp_dir, filename), 'w') as f:
                    f.write("test content")
            
            # Create subdirectory
            subdir = os.path.join(temp_dir, 'subdir')
            os.makedirs(subdir)
            
            files = get_files_in_directory(temp_dir)
            
            # Should include all files plus subdirectory
            assert len(files) >= len(test_files) + 1
            
            # Check file types are detected
            file_names = [f['name'] for f in files]
            for test_file in test_files:
                assert test_file in file_names
            assert 'subdir' in file_names

    def test_empty_directory_listing(self):
        """Test listing an empty directory"""
        with tempfile.TemporaryDirectory() as temp_dir:
            files = get_files_in_directory(temp_dir)
            assert files == []

    def test_directory_with_hidden_files(self):
        """Test directory listing with hidden files (Unix-style)"""
        with tempfile.TemporaryDirectory() as temp_dir:
            # Create hidden file
            hidden_file = os.path.join(temp_dir, '.hidden')
            with open(hidden_file, 'w') as f:
                f.write("hidden content")
            
            # Create regular file
            regular_file = os.path.join(temp_dir, 'regular.txt')
            with open(regular_file, 'w') as f:
                f.write("regular content")
            
            files = get_files_in_directory(temp_dir)
            
            # Should include both files (hidden file handling depends on implementation)
            file_names = [f['name'] for f in files]
            assert 'regular.txt' in file_names
            # Hidden file inclusion is implementation-dependent

    def test_large_directory_listing(self):
        """Test listing directory with many files"""
        with tempfile.TemporaryDirectory() as temp_dir:
            # Create many files
            for i in range(50):
                filename = f'file_{i:03d}.txt'
                with open(os.path.join(temp_dir, filename), 'w') as f:
                    f.write(f"content {i}")
            
            files = get_files_in_directory(temp_dir)
            
            assert len(files) == 50
            # Files should be returned (order may vary)
            file_names = [f['name'] for f in files]
            assert 'file_000.txt' in file_names
            assert 'file_049.txt' in file_names
