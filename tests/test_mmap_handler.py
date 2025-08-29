"""
Unit tests for MMapFileHandler class in aird.main module.
"""

import os
import tempfile
import pytest
from unittest.mock import patch, mock_open
from aird.main import MMapFileHandler, MMAP_MIN_SIZE, CHUNK_SIZE


class TestMMapFileHandler:
    """Test MMapFileHandler class methods"""
    
    def test_should_use_mmap_large_file(self):
        """Test should_use_mmap returns True for large files"""
        large_size = MMAP_MIN_SIZE + 1000
        result = MMapFileHandler.should_use_mmap(large_size)
        assert result is True
    
    def test_should_use_mmap_small_file(self):
        """Test should_use_mmap returns False for small files"""
        small_size = MMAP_MIN_SIZE - 1
        result = MMapFileHandler.should_use_mmap(small_size)
        assert result is False
    
    def test_should_use_mmap_exact_threshold(self):
        """Test should_use_mmap at exact threshold"""
        exact_size = MMAP_MIN_SIZE
        result = MMapFileHandler.should_use_mmap(exact_size)
        assert result is True


class TestServeFileChunk:
    """Test serve_file_chunk method"""
    
    @pytest.mark.asyncio
    async def test_serve_small_file_complete(self):
        """Test serving complete small file"""
        content = b"This is a small test file content."
        
        with tempfile.NamedTemporaryFile(delete=False) as tmp:
            tmp.write(content)
            tmp.flush()
            
            try:
                chunks = []
                async for chunk in MMapFileHandler.serve_file_chunk(tmp.name):
                    chunks.append(chunk)
                
                result = b''.join(chunks)
                assert result == content
            finally:
                os.unlink(tmp.name)
    
    @pytest.mark.asyncio
    async def test_serve_small_file_partial(self):
        """Test serving partial small file"""
        content = b"This is a small test file content."
        start = 5
        end = 15
        
        with tempfile.NamedTemporaryFile(delete=False) as tmp:
            tmp.write(content)
            tmp.flush()
            
            try:
                chunks = []
                async for chunk in MMapFileHandler.serve_file_chunk(tmp.name, start=start, end=end):
                    chunks.append(chunk)
                
                result = b''.join(chunks)
                expected = content[start:end+1]
                assert result == expected
            finally:
                os.unlink(tmp.name)
    
    @pytest.mark.asyncio
    async def test_serve_large_file_complete(self):
        """Test serving complete large file"""
        # Create a large file that will use mmap
        content = b"A" * (MMAP_MIN_SIZE + 1000)
        
        with tempfile.NamedTemporaryFile(delete=False) as tmp:
            tmp.write(content)
            tmp.flush()
            
            try:
                chunks = []
                async for chunk in MMapFileHandler.serve_file_chunk(tmp.name, chunk_size=1000):
                    chunks.append(chunk)
                
                result = b''.join(chunks)
                assert result == content
                assert len(chunks) > 1  # Should be served in multiple chunks
            finally:
                os.unlink(tmp.name)
    
    @pytest.mark.asyncio
    async def test_serve_large_file_partial(self):
        """Test serving partial large file"""
        # Create a large file that will use mmap
        content = b"B" * (MMAP_MIN_SIZE + 1000)
        start = 1000
        end = 2000
        
        with tempfile.NamedTemporaryFile(delete=False) as tmp:
            tmp.write(content)
            tmp.flush()
            
            try:
                chunks = []
                async for chunk in MMapFileHandler.serve_file_chunk(tmp.name, start=start, end=end, chunk_size=500):
                    chunks.append(chunk)
                
                result = b''.join(chunks)
                expected = content[start:end+1]
                assert result == expected
            finally:
                os.unlink(tmp.name)
    
    @pytest.mark.asyncio
    async def test_serve_file_nonexistent(self):
        """Test serving non-existent file"""
        with pytest.raises(FileNotFoundError):
            chunks = []
            async for chunk in MMapFileHandler.serve_file_chunk("/nonexistent/file.txt"):
                chunks.append(chunk)


class TestFindLineOffsets:
    """Test find_line_offsets method"""
    
    def test_find_line_offsets_small_file(self):
        """Test finding line offsets in small file"""
        content = "Line 1\nLine 2\nLine 3\n"
        
        with tempfile.NamedTemporaryFile(mode='w', delete=False) as tmp:
            tmp.write(content)
            tmp.flush()
            
            try:
                offsets = MMapFileHandler.find_line_offsets(tmp.name)
                expected = [0, 7, 14]  # Start of each line
                assert offsets == expected
            finally:
                os.unlink(tmp.name)
    
    def test_find_line_offsets_large_file(self):
        """Test finding line offsets in large file"""
        # Create large file with known line structure
        lines = [f"Line {i}\n" for i in range(1, 1000)]
        content = "".join(lines)
        
        # Make it large enough to trigger mmap
        padding = "X" * (MMAP_MIN_SIZE + 1000)
        content = content + padding
        
        with tempfile.NamedTemporaryFile(mode='w', delete=False) as tmp:
            tmp.write(content)
            tmp.flush()
            
            try:
                offsets = MMapFileHandler.find_line_offsets(tmp.name, max_lines=10)
                assert len(offsets) <= 10
                assert offsets[0] == 0  # First line always starts at 0
                # Check that offsets are increasing
                assert all(offsets[i] < offsets[i+1] for i in range(len(offsets)-1))
            finally:
                os.unlink(tmp.name)
    
    def test_find_line_offsets_empty_file(self):
        """Test finding line offsets in empty file"""
        with tempfile.NamedTemporaryFile(delete=False) as tmp:
            tmp.flush()
            
            try:
                offsets = MMapFileHandler.find_line_offsets(tmp.name)
                assert offsets == [0]
            finally:
                os.unlink(tmp.name)
    
    def test_find_line_offsets_single_line(self):
        """Test finding line offsets in single line file"""
        content = "Single line without newline"
        
        with tempfile.NamedTemporaryFile(mode='w', delete=False) as tmp:
            tmp.write(content)
            tmp.flush()
            
            try:
                offsets = MMapFileHandler.find_line_offsets(tmp.name)
                assert offsets == [0]
            finally:
                os.unlink(tmp.name)
    
    def test_find_line_offsets_max_lines_limit(self):
        """Test max_lines parameter"""
        content = "Line 1\nLine 2\nLine 3\nLine 4\nLine 5\n"
        
        with tempfile.NamedTemporaryFile(mode='w', delete=False) as tmp:
            tmp.write(content)
            tmp.flush()
            
            try:
                offsets = MMapFileHandler.find_line_offsets(tmp.name, max_lines=3)
                assert len(offsets) <= 3
            finally:
                os.unlink(tmp.name)


class TestSearchInFile:
    """Test search_in_file method"""
    
    def test_search_in_small_file(self):
        """Test searching in small file"""
        content = "Line 1 with test\nLine 2 without match\nLine 3 with test again\n"
        
        with tempfile.NamedTemporaryFile(mode='w', delete=False) as tmp:
            tmp.write(content)
            tmp.flush()
            
            try:
                results = MMapFileHandler.search_in_file(tmp.name, "test")
                assert len(results) == 2
                
                # Check first result
                assert results[0]["line_number"] == 1
                assert "test" in results[0]["line_content"]
                assert len(results[0]["match_positions"]) >= 1
                
                # Check second result
                assert results[1]["line_number"] == 3
                assert "test" in results[1]["line_content"]
                assert len(results[1]["match_positions"]) >= 1
            finally:
                os.unlink(tmp.name)
    
    def test_search_in_large_file(self):
        """Test searching in large file"""
        # Create large file with known search terms
        lines = []
        for i in range(1000):
            if i % 100 == 0:
                lines.append(f"Line {i} contains searchterm\n")
            else:
                lines.append(f"Line {i} regular content\n")
        
        content = "".join(lines)
        # Make it large enough to trigger mmap
        padding = "X" * (MMAP_MIN_SIZE + 1000)
        content = content + padding
        
        with tempfile.NamedTemporaryFile(mode='w', delete=False) as tmp:
            tmp.write(content)
            tmp.flush()
            
            try:
                results = MMapFileHandler.search_in_file(tmp.name, "searchterm", max_results=5)
                assert len(results) <= 5
                assert len(results) > 0
                
                # All results should contain the search term
                for result in results:
                    assert "searchterm" in result["line_content"]
                    assert result["line_number"] > 0
                    assert len(result["match_positions"]) > 0
            finally:
                os.unlink(tmp.name)
    
    def test_search_no_matches(self):
        """Test searching with no matches"""
        content = "Line 1\nLine 2\nLine 3\n"
        
        with tempfile.NamedTemporaryFile(mode='w', delete=False) as tmp:
            tmp.write(content)
            tmp.flush()
            
            try:
                results = MMapFileHandler.search_in_file(tmp.name, "nonexistent")
                assert results == []
            finally:
                os.unlink(tmp.name)
    
    def test_search_multiple_matches_per_line(self):
        """Test searching with multiple matches per line"""
        content = "test line with test and test again\n"
        
        with tempfile.NamedTemporaryFile(mode='w', delete=False) as tmp:
            tmp.write(content)
            tmp.flush()
            
            try:
                results = MMapFileHandler.search_in_file(tmp.name, "test")
                assert len(results) == 1
                assert len(results[0]["match_positions"]) == 3  # Three occurrences
            finally:
                os.unlink(tmp.name)
    
    def test_search_max_results_limit(self):
        """Test max_results parameter"""
        lines = [f"Line {i} with test\n" for i in range(100)]
        content = "".join(lines)
        
        with tempfile.NamedTemporaryFile(mode='w', delete=False) as tmp:
            tmp.write(content)
            tmp.flush()
            
            try:
                results = MMapFileHandler.search_in_file(tmp.name, "test", max_results=10)
                assert len(results) == 10
            finally:
                os.unlink(tmp.name)
    
    def test_search_binary_file_fallback(self):
        """Test searching in binary file falls back gracefully"""
        # Create a file with some binary content
        binary_content = b'\x00\x01\x02\x03test\x04\x05\x06\x07'
        
        with tempfile.NamedTemporaryFile(delete=False) as tmp:
            tmp.write(binary_content)
            tmp.flush()
            
            try:
                # Should not raise exception, might find or not find matches
                results = MMapFileHandler.search_in_file(tmp.name, "test")
                assert isinstance(results, list)
            finally:
                os.unlink(tmp.name)
    
    def test_search_unicode_content(self):
        """Test searching in file with Unicode content"""
        content = "Line with émojis 🚀 and test content\nAnother line with tëst\n"
        
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', delete=False) as tmp:
            tmp.write(content)
            tmp.flush()
            
            try:
                results = MMapFileHandler.search_in_file(tmp.name, "test")
                assert len(results) >= 1
                # Should handle Unicode gracefully
                for result in results:
                    assert isinstance(result["line_content"], str)
            finally:
                os.unlink(tmp.name)