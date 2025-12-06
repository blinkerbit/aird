"""Tests for aird/core/mmap_handler.py"""

import pytest
import os
import tempfile
from unittest.mock import patch, MagicMock

from aird.core.mmap_handler import (
    MMapFileHandler,
    get_files_in_directory,
    is_video_file,
    is_audio_file,
)


class TestMMapFileHandlerShouldUseMmap:
    """Tests for MMapFileHandler.should_use_mmap"""
    
    def test_small_file_no_mmap(self):
        """Test that small files don't use mmap"""
        # Default MMAP_MIN_SIZE is 1MB (1024 * 1024)
        assert MMapFileHandler.should_use_mmap(100) is False
        assert MMapFileHandler.should_use_mmap(1000) is False
        assert MMapFileHandler.should_use_mmap(500000) is False
    
    def test_large_file_uses_mmap(self):
        """Test that large files use mmap"""
        # Files >= MMAP_MIN_SIZE should use mmap
        from aird.constants import MMAP_MIN_SIZE
        
        assert MMapFileHandler.should_use_mmap(MMAP_MIN_SIZE) is True
        assert MMapFileHandler.should_use_mmap(MMAP_MIN_SIZE + 1) is True
        assert MMapFileHandler.should_use_mmap(MMAP_MIN_SIZE * 10) is True
    
    def test_boundary_condition(self):
        """Test boundary condition at MMAP_MIN_SIZE"""
        from aird.constants import MMAP_MIN_SIZE
        
        assert MMapFileHandler.should_use_mmap(MMAP_MIN_SIZE - 1) is False
        assert MMapFileHandler.should_use_mmap(MMAP_MIN_SIZE) is True


class TestMMapFileHandlerFindLineOffsets:
    """Tests for MMapFileHandler.find_line_offsets"""
    
    def test_find_offsets_simple_file(self):
        """Test finding line offsets in a simple file"""
        with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.txt') as f:
            f.write("line1\n")
            f.write("line2\n")
            f.write("line3\n")
            temp_path = f.name
        
        try:
            offsets = MMapFileHandler.find_line_offsets(temp_path)
            
            # Should have offsets for 3 lines
            assert len(offsets) >= 3
            assert 0 in offsets  # First line starts at 0
        finally:
            os.unlink(temp_path)
    
    def test_find_offsets_empty_file(self):
        """Test finding line offsets in an empty file"""
        with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.txt') as f:
            temp_path = f.name
        
        try:
            offsets = MMapFileHandler.find_line_offsets(temp_path)
            # Empty file should have at least offset 0
            assert 0 in offsets or len(offsets) == 0
        finally:
            os.unlink(temp_path)
    
    def test_find_offsets_with_max_lines(self):
        """Test finding line offsets with max_lines limit"""
        with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.txt') as f:
            for i in range(100):
                f.write(f"line{i}\n")
            temp_path = f.name
        
        try:
            offsets = MMapFileHandler.find_line_offsets(temp_path, max_lines=10)
            assert len(offsets) <= 10
        finally:
            os.unlink(temp_path)
    
    def test_find_offsets_single_line_no_newline(self):
        """Test file with single line and no trailing newline"""
        with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.txt') as f:
            f.write("single line without newline")
            temp_path = f.name
        
        try:
            offsets = MMapFileHandler.find_line_offsets(temp_path)
            assert 0 in offsets
        finally:
            os.unlink(temp_path)
    
    def test_find_offsets_preserves_order(self):
        """Test that offsets are in ascending order"""
        with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.txt') as f:
            f.write("line1\n")
            f.write("line2\n")
            f.write("line3\n")
            temp_path = f.name
        
        try:
            offsets = MMapFileHandler.find_line_offsets(temp_path)
            
            # Offsets should be in ascending order
            for i in range(len(offsets) - 1):
                assert offsets[i] < offsets[i + 1]
        finally:
            os.unlink(temp_path)


class TestMMapFileHandlerSearchInFile:
    """Tests for MMapFileHandler.search_in_file"""
    
    def test_search_basic_match(self):
        """Test basic search functionality"""
        with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.txt') as f:
            f.write("hello world\n")
            f.write("goodbye world\n")
            f.write("hello again\n")
            temp_path = f.name
        
        try:
            results = MMapFileHandler.search_in_file(temp_path, "hello")
            
            assert len(results) == 2
            assert results[0]['line_number'] == 1
            assert results[1]['line_number'] == 3
        finally:
            os.unlink(temp_path)
    
    def test_search_no_matches(self):
        """Test search with no matches"""
        with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.txt') as f:
            f.write("hello world\n")
            f.write("goodbye world\n")
            temp_path = f.name
        
        try:
            results = MMapFileHandler.search_in_file(temp_path, "notfound")
            assert len(results) == 0
        finally:
            os.unlink(temp_path)
    
    def test_search_with_max_results(self):
        """Test search with max_results limit"""
        with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.txt') as f:
            for i in range(100):
                f.write(f"test line {i}\n")
            temp_path = f.name
        
        try:
            results = MMapFileHandler.search_in_file(temp_path, "test", max_results=5)
            assert len(results) == 5
        finally:
            os.unlink(temp_path)
    
    def test_search_match_positions(self):
        """Test that match positions are correctly reported"""
        with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.txt') as f:
            f.write("hello hello hello\n")
            temp_path = f.name
        
        try:
            results = MMapFileHandler.search_in_file(temp_path, "hello")
            
            assert len(results) == 1
            assert len(results[0]['match_positions']) == 3
            assert 0 in results[0]['match_positions']  # First match at position 0
        finally:
            os.unlink(temp_path)
    
    def test_search_line_content_preserved(self):
        """Test that line content is correctly preserved"""
        content = "The quick brown fox"
        with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.txt') as f:
            f.write(content + "\n")
            temp_path = f.name
        
        try:
            results = MMapFileHandler.search_in_file(temp_path, "quick")
            
            assert len(results) == 1
            assert results[0]['line_content'] == content
        finally:
            os.unlink(temp_path)
    
    def test_search_empty_file(self):
        """Test search in empty file"""
        with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.txt') as f:
            temp_path = f.name
        
        try:
            results = MMapFileHandler.search_in_file(temp_path, "test")
            assert len(results) == 0
        finally:
            os.unlink(temp_path)
    
    def test_search_case_sensitive(self):
        """Test that search is case sensitive"""
        with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.txt') as f:
            f.write("Hello World\n")
            f.write("hello world\n")
            f.write("HELLO WORLD\n")
            temp_path = f.name
        
        try:
            results = MMapFileHandler.search_in_file(temp_path, "Hello")
            assert len(results) == 1
            assert results[0]['line_number'] == 1
        finally:
            os.unlink(temp_path)
    
    def test_search_unicode_content(self):
        """Test search with unicode content"""
        with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.txt', encoding='utf-8') as f:
            f.write("Héllo wörld\n")
            f.write("日本語テスト\n")
            f.write("Normal text\n")
            temp_path = f.name
        
        try:
            results = MMapFileHandler.search_in_file(temp_path, "Héllo")
            assert len(results) == 1
            
            results = MMapFileHandler.search_in_file(temp_path, "日本語")
            assert len(results) == 1
        finally:
            os.unlink(temp_path)


class TestGetFilesInDirectory:
    """Tests for get_files_in_directory function"""
    
    def test_get_files_basic(self):
        """Test getting files in a directory"""
        with tempfile.TemporaryDirectory() as temp_dir:
            # Create test files
            with open(os.path.join(temp_dir, "file1.txt"), 'w') as f:
                f.write("content1")
            with open(os.path.join(temp_dir, "file2.txt"), 'w') as f:
                f.write("content2")
            
            files = get_files_in_directory(temp_dir)
            
            assert len(files) == 2
            names = [f['name'] for f in files]
            assert "file1.txt" in names
            assert "file2.txt" in names
    
    def test_get_files_includes_directories(self):
        """Test that directories are included in results"""
        with tempfile.TemporaryDirectory() as temp_dir:
            os.makedirs(os.path.join(temp_dir, "subdir"))
            with open(os.path.join(temp_dir, "file.txt"), 'w') as f:
                f.write("content")
            
            files = get_files_in_directory(temp_dir)
            
            dirs = [f for f in files if f['is_dir']]
            assert len(dirs) == 1
            assert dirs[0]['name'] == "subdir"
    
    def test_get_files_metadata(self):
        """Test that file metadata is included"""
        with tempfile.TemporaryDirectory() as temp_dir:
            with open(os.path.join(temp_dir, "test.txt"), 'w') as f:
                f.write("test content")
            
            files = get_files_in_directory(temp_dir)
            
            assert len(files) == 1
            file_info = files[0]
            
            assert 'name' in file_info
            assert 'is_dir' in file_info
            assert 'size_bytes' in file_info
            assert 'size_str' in file_info
            assert 'modified' in file_info
            assert 'modified_timestamp' in file_info
    
    def test_get_files_size_formatting(self):
        """Test that file size is correctly formatted"""
        with tempfile.TemporaryDirectory() as temp_dir:
            with open(os.path.join(temp_dir, "test.txt"), 'w') as f:
                f.write("x" * 2048)  # 2KB
            
            files = get_files_in_directory(temp_dir)
            
            file_info = files[0]
            assert file_info['size_bytes'] == 2048
            assert "KB" in file_info['size_str']
    
    def test_get_files_directory_size(self):
        """Test that directory size shows as dash"""
        with tempfile.TemporaryDirectory() as temp_dir:
            os.makedirs(os.path.join(temp_dir, "subdir"))
            
            files = get_files_in_directory(temp_dir)
            
            dir_info = [f for f in files if f['is_dir']][0]
            assert dir_info['size_str'] == "-"
    
    def test_get_files_empty_directory(self):
        """Test getting files in empty directory"""
        with tempfile.TemporaryDirectory() as temp_dir:
            files = get_files_in_directory(temp_dir)
            assert len(files) == 0


class TestIsVideoFile:
    """Tests for is_video_file function"""
    
    def test_common_video_extensions(self):
        """Test common video file extensions"""
        video_files = [
            "movie.mp4",
            "video.avi",
            "film.mkv",
            "clip.mov",
            "recording.wmv",
            "stream.flv",
            "web_video.webm",
            "mobile.m4v",
            "phone.3gp",
            "open.ogv"
        ]
        
        for filename in video_files:
            assert is_video_file(filename) is True, f"{filename} should be detected as video"
    
    def test_non_video_extensions(self):
        """Test non-video file extensions"""
        non_video_files = [
            "document.txt",
            "image.jpg",
            "audio.mp3",
            "archive.zip",
            "script.py",
            "data.json"
        ]
        
        for filename in non_video_files:
            assert is_video_file(filename) is False, f"{filename} should not be detected as video"
    
    def test_case_insensitive(self):
        """Test that extension detection is case insensitive"""
        assert is_video_file("movie.MP4") is True
        assert is_video_file("video.AVI") is True
        assert is_video_file("film.MkV") is True
    
    def test_no_extension(self):
        """Test file without extension"""
        assert is_video_file("noextension") is False
    
    def test_hidden_file(self):
        """Test hidden file with video extension"""
        assert is_video_file(".hidden.mp4") is True


class TestIsAudioFile:
    """Tests for is_audio_file function"""
    
    def test_common_audio_extensions(self):
        """Test common audio file extensions"""
        audio_files = [
            "song.mp3",
            "audio.wav",
            "music.flac",
            "track.aac",
            "podcast.ogg",
            "itunes.m4a",
            "windows.wma"
        ]
        
        for filename in audio_files:
            assert is_audio_file(filename) is True, f"{filename} should be detected as audio"
    
    def test_non_audio_extensions(self):
        """Test non-audio file extensions"""
        non_audio_files = [
            "document.txt",
            "video.mp4",
            "image.png",
            "archive.tar",
            "script.js"
        ]
        
        for filename in non_audio_files:
            assert is_audio_file(filename) is False, f"{filename} should not be detected as audio"
    
    def test_case_insensitive(self):
        """Test that extension detection is case insensitive"""
        assert is_audio_file("song.MP3") is True
        assert is_audio_file("audio.WAV") is True
        assert is_audio_file("music.FlAc") is True
    
    def test_no_extension(self):
        """Test file without extension"""
        assert is_audio_file("noextension") is False


class TestMMapFileHandlerServeFileChunk:
    """Tests for MMapFileHandler.serve_file_chunk (async generator)"""
    
    @pytest.mark.asyncio
    async def test_serve_small_file(self):
        """Test serving a small file (uses traditional method)"""
        content = b"Hello, World!" * 100
        
        with tempfile.NamedTemporaryFile(mode='wb', delete=False, suffix='.bin') as f:
            f.write(content)
            temp_path = f.name
        
        try:
            chunks = []
            async for chunk in MMapFileHandler.serve_file_chunk(temp_path):
                chunks.append(chunk)
            
            result = b''.join(chunks)
            assert result == content
        finally:
            os.unlink(temp_path)
    
    @pytest.mark.asyncio
    async def test_serve_with_range(self):
        """Test serving file with start/end range"""
        content = b"0123456789" * 100
        
        with tempfile.NamedTemporaryFile(mode='wb', delete=False, suffix='.bin') as f:
            f.write(content)
            temp_path = f.name
        
        try:
            chunks = []
            async for chunk in MMapFileHandler.serve_file_chunk(temp_path, start=10, end=19):
                chunks.append(chunk)
            
            result = b''.join(chunks)
            assert len(result) == 10
        finally:
            os.unlink(temp_path)
    
    @pytest.mark.asyncio
    async def test_serve_empty_file(self):
        """Test serving an empty file"""
        with tempfile.NamedTemporaryFile(mode='wb', delete=False, suffix='.bin') as f:
            temp_path = f.name
        
        try:
            chunks = []
            async for chunk in MMapFileHandler.serve_file_chunk(temp_path):
                chunks.append(chunk)
            
            result = b''.join(chunks)
            assert result == b''
        finally:
            os.unlink(temp_path)
