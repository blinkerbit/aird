"""
Unit tests for MMapFileHandler class in aird.main module.

These tests cover memory-mapped file handling, efficient file serving,
line offset calculation, and file search functionality.
"""

import os
import tempfile
import pytest
import asyncio
from unittest.mock import patch, mock_open

# Import with error handling for missing module
try:
    from aird.main import MMapFileHandler, MMAP_MIN_SIZE, CHUNK_SIZE
    AIRD_AVAILABLE = True
except ImportError:
    AIRD_AVAILABLE = False


@pytest.mark.skipif(not AIRD_AVAILABLE, reason="aird.main module not available")
class TestMMapFileHandlerUtility:
    """Test MMapFileHandler utility methods"""
    
    @pytest.mark.parametrize("file_size,expected", [
        (MMAP_MIN_SIZE + 1000, True),
        (MMAP_MIN_SIZE + 1, True),
        (MMAP_MIN_SIZE, True),
        (MMAP_MIN_SIZE - 1, False),
        (1024, False),
        (0, False),
    ])
    def test_should_use_mmap(self, file_size, expected):
        """Test should_use_mmap returns correct value for different file sizes"""
        result = MMapFileHandler.should_use_mmap(file_size)
        assert result == expected, f"Expected {expected} for size {file_size}"
    
    def test_should_use_mmap_edge_cases(self):
        """Test should_use_mmap with edge cases"""
        # Test with negative size (shouldn't happen but should handle gracefully)
        try:
            result = MMapFileHandler.should_use_mmap(-1)
            assert result is False, "Negative size should return False"
        except (ValueError, TypeError):
            # Acceptable if function validates input
            pass
        
        # Test with very large size
        huge_size = MMAP_MIN_SIZE * 1000000
        result = MMapFileHandler.should_use_mmap(huge_size)
        assert result is True, "Very large size should use mmap"
    
    def test_constants_are_reasonable(self):
        """Test that the constants have reasonable values"""
        assert isinstance(MMAP_MIN_SIZE, int), "MMAP_MIN_SIZE should be integer"
        assert MMAP_MIN_SIZE > 0, "MMAP_MIN_SIZE should be positive"
        assert isinstance(CHUNK_SIZE, int), "CHUNK_SIZE should be integer"
        assert CHUNK_SIZE > 0, "CHUNK_SIZE should be positive"
        assert CHUNK_SIZE <= MMAP_MIN_SIZE, "CHUNK_SIZE should be <= MMAP_MIN_SIZE"


@pytest.mark.skipif(not AIRD_AVAILABLE, reason="aird.main module not available")
class TestServeFileChunk:
    """Test serve_file_chunk method"""
    
    @pytest.mark.asyncio
    async def test_serve_small_file_complete(self, temp_dir):
        """Test serving complete small file"""
        content = b"This is a small test file content for testing."
        
        file_path = os.path.join(temp_dir, "small_file.txt")
        with open(file_path, "wb") as f:
            f.write(content)
        
        chunks = []
        async for chunk in MMapFileHandler.serve_file_chunk(file_path):
            chunks.append(chunk)
        
        result = b''.join(chunks)
        assert result == content, "Served content doesn't match original"
        assert len(chunks) >= 1, "Should produce at least one chunk"
    
    @pytest.mark.asyncio
    async def test_serve_small_file_partial(self, temp_dir):
        """Test serving partial small file"""
        content = b"This is a small test file content for partial serving."
        start = 10
        end = 25
        
        file_path = os.path.join(temp_dir, "small_file.txt")
        with open(file_path, "wb") as f:
            f.write(content)
        
        chunks = []
        async for chunk in MMapFileHandler.serve_file_chunk(file_path, start=start, end=end):
            chunks.append(chunk)
        
        result = b''.join(chunks)
        expected = content[start:end+1]
        assert result == expected, f"Expected {expected!r}, got {result!r}"
    
    @pytest.mark.asyncio
    async def test_serve_large_file_complete(self, temp_dir):
        """Test serving complete large file that uses mmap"""
        # Create a smaller large file that will trigger mmap usage but be manageable
        content = b"A" * (MMAP_MIN_SIZE + 1000)
        
        file_path = os.path.join(temp_dir, "large_file.dat")
        with open(file_path, "wb") as f:
            f.write(content)
        
        chunks = []
        chunk_size = 8192  # Larger chunk size for efficiency
        expected_chunks = (len(content) + chunk_size - 1) // chunk_size  # Ceiling division
        
        async for chunk in MMapFileHandler.serve_file_chunk(file_path, chunk_size=chunk_size):
            chunks.append(chunk)
            # Safety check to prevent infinite loops, but allow enough chunks
            if len(chunks) > expected_chunks + 10:
                break
        
        result = b''.join(chunks)
        assert result == content, f"Large file content mismatch. Expected {len(content)} bytes, got {len(result)} bytes"
        assert len(chunks) > 1, "Large file should be served in multiple chunks"
        
        # Check chunk sizes are reasonable
        for i, chunk in enumerate(chunks[:-1]):  # All but last chunk
            assert len(chunk) <= chunk_size, f"Chunk {i} too large: {len(chunk)} > {chunk_size}"
    
    @pytest.mark.asyncio
    async def test_serve_large_file_partial(self, temp_dir):
        """Test serving partial large file"""
        # Create a large file
        content = b"B" * (MMAP_MIN_SIZE + 2000)
        start = 1000
        end = 2000
        
        file_path = os.path.join(temp_dir, "large_file.dat")
        with open(file_path, "wb") as f:
            f.write(content)
        
        chunks = []
        async for chunk in MMapFileHandler.serve_file_chunk(
            file_path, start=start, end=end, chunk_size=500
        ):
            chunks.append(chunk)
        
        result = b''.join(chunks)
        expected = content[start:end+1]
        assert result == expected, "Partial large file content mismatch"
    
    @pytest.mark.asyncio
    async def test_serve_file_nonexistent(self):
        """Test serving non-existent file"""
        with pytest.raises(FileNotFoundError):
            chunks = []
            async for chunk in MMapFileHandler.serve_file_chunk("/nonexistent/file.txt"):
                chunks.append(chunk)
    
    @pytest.mark.asyncio
    async def test_serve_empty_file(self, temp_dir):
        """Test serving empty file"""
        file_path = os.path.join(temp_dir, "empty_file.txt")
        with open(file_path, "wb") as f:
            pass  # Create empty file
        
        chunks = []
        async for chunk in MMapFileHandler.serve_file_chunk(file_path):
            chunks.append(chunk)
        
        result = b''.join(chunks)
        assert result == b"", "Empty file should return empty content"
    
    @pytest.mark.asyncio
    async def test_serve_file_invalid_range(self, temp_dir):
        """Test serving file with invalid byte range"""
        content = b"Test content for range testing"
        
        file_path = os.path.join(temp_dir, "test_file.txt")
        with open(file_path, "wb") as f:
            f.write(content)
        
        # Test start > end
        chunks = []
        async for chunk in MMapFileHandler.serve_file_chunk(file_path, start=20, end=10):
            chunks.append(chunk)
        
        result = b''.join(chunks)
        # Should handle gracefully (either empty or error)
        assert isinstance(result, bytes)
        
        # Test start beyond file size
        chunks = []
        async for chunk in MMapFileHandler.serve_file_chunk(file_path, start=1000):
            chunks.append(chunk)
        
        result = b''.join(chunks)
        assert result == b"", "Start beyond file size should return empty"
    
    @pytest.mark.asyncio
    async def test_serve_file_custom_chunk_size(self, temp_dir):
        """Test serving file with custom chunk size"""
        content = b"X" * 5000  # 5KB file
        
        file_path = os.path.join(temp_dir, "test_file.dat")
        with open(file_path, "wb") as f:
            f.write(content)
        
        # Test with small chunk size
        chunks = []
        custom_chunk_size = 100
        async for chunk in MMapFileHandler.serve_file_chunk(file_path, chunk_size=custom_chunk_size):
            chunks.append(chunk)
        
        result = b''.join(chunks)
        assert result == content, "Custom chunk size content mismatch"
        
        # Most chunks should be the custom size (except possibly the last)
        for chunk in chunks[:-1]:
            assert len(chunk) <= custom_chunk_size


@pytest.mark.skipif(not AIRD_AVAILABLE, reason="aird.main module not available")
class TestFindLineOffsets:
    """Test find_line_offsets method"""
    
    def test_find_line_offsets_small_file(self, temp_dir):
        """Test finding line offsets in small file"""
        content = "Line 1\nLine 2\nLine 3\n"
        
        file_path = os.path.join(temp_dir, "small_lines.txt")
        # Write in binary mode to avoid platform-specific line ending conversion
        with open(file_path, "wb") as f:
            f.write(content.encode('utf-8'))
        
        offsets = MMapFileHandler.find_line_offsets(file_path)
        expected = [0, 7, 14]  # Start of each line
        assert offsets == expected, f"Expected {expected}, got {offsets}"
    
    def test_find_line_offsets_large_file(self, temp_dir, large_file):
        """Test finding line offsets in large file"""
        offsets = MMapFileHandler.find_line_offsets(large_file, max_lines=10)
        
        assert len(offsets) <= 10, "Should respect max_lines limit"
        assert offsets[0] == 0, "First line should always start at 0"
        
        # Check that offsets are increasing
        for i in range(len(offsets) - 1):
            assert offsets[i] < offsets[i + 1], f"Offsets not increasing: {offsets}"
    
    def test_find_line_offsets_empty_file(self, temp_dir):
        """Test finding line offsets in empty file"""
        file_path = os.path.join(temp_dir, "empty.txt")
        with open(file_path, "w", encoding='utf-8') as f:
            pass  # Create empty file
        
        offsets = MMapFileHandler.find_line_offsets(file_path)
        assert offsets == [], "Empty file should have no line offsets"
    
    def test_find_line_offsets_single_line_no_newline(self, temp_dir):
        """Test finding line offsets in single line file without newline"""
        content = "Single line without newline"
        
        file_path = os.path.join(temp_dir, "single_line.txt")
        with open(file_path, "w", encoding='utf-8') as f:
            f.write(content)
        
        offsets = MMapFileHandler.find_line_offsets(file_path)
        assert offsets == [0], "Single line file should have offset [0]"
    
    def test_find_line_offsets_single_line_with_newline(self, temp_dir):
        """Test finding line offsets in single line file with newline"""
        content = "Single line with newline\n"
        
        file_path = os.path.join(temp_dir, "single_line.txt")
        with open(file_path, "w", encoding='utf-8') as f:
            f.write(content)
        
        offsets = MMapFileHandler.find_line_offsets(file_path)
        assert offsets == [0], "Single line with newline should have offset [0]"
    
    def test_find_line_offsets_max_lines_limit(self, temp_dir):
        """Test max_lines parameter"""
        content = "Line 1\nLine 2\nLine 3\nLine 4\nLine 5\n"
        
        file_path = os.path.join(temp_dir, "five_lines.txt")
        with open(file_path, "w", encoding='utf-8') as f:
            f.write(content)
        
        offsets = MMapFileHandler.find_line_offsets(file_path, max_lines=3)
        assert len(offsets) <= 3, "Should respect max_lines=3"
        assert len(offsets) >= 1, "Should find at least one line"
    
    def test_find_line_offsets_different_line_endings(self, temp_dir):
        """Test finding line offsets with different line ending styles"""
        test_cases = [
            ("unix_lines.txt", "Line 1\nLine 2\nLine 3\n"),
            ("windows_lines.txt", "Line 1\r\nLine 2\r\nLine 3\r\n"),
            ("mac_lines.txt", "Line 1\rLine 2\rLine 3\r"),
            ("mixed_lines.txt", "Line 1\nLine 2\r\nLine 3\rLine 4\n")
        ]
        
        for filename, content in test_cases:
            file_path = os.path.join(temp_dir, filename)
            with open(file_path, "w", encoding='utf-8', newline='') as f:
                f.write(content)
            
            offsets = MMapFileHandler.find_line_offsets(file_path)
            
            # Should find reasonable number of lines
            assert len(offsets) >= 1, f"Should find lines in {filename}"
            assert offsets[0] == 0, f"First line should start at 0 in {filename}"
    
    def test_find_line_offsets_unicode_content(self, temp_dir):
        """Test finding line offsets in file with Unicode content"""
        content = "Line 1 with émojis 🚀\nLine 2 with ñ and ü\nLine 3 normal\n"
        
        file_path = os.path.join(temp_dir, "unicode_lines.txt")
        with open(file_path, "w", encoding='utf-8') as f:
            f.write(content)
        
        offsets = MMapFileHandler.find_line_offsets(file_path)
        
        # Should handle Unicode gracefully
        assert len(offsets) >= 1, "Should find lines in Unicode file"
        assert offsets[0] == 0, "First line should start at 0"
        assert all(isinstance(offset, int) for offset in offsets), "All offsets should be integers"


@pytest.mark.skipif(not AIRD_AVAILABLE, reason="aird.main module not available")
class TestSearchInFile:
    """Test search_in_file method"""
    
    def test_search_in_small_file(self, temp_dir):
        """Test searching in small file"""
        content = "Line 1 with test\nLine 2 without match\nLine 3 with test again\n"
        
        file_path = os.path.join(temp_dir, "search_small.txt")
        with open(file_path, "w", encoding='utf-8') as f:
            f.write(content)
        
        results = MMapFileHandler.search_in_file(file_path, "test")
        
        assert len(results) == 2, f"Expected 2 results, got {len(results)}"
        
        # Check first result
        assert results[0]["line_number"] == 1
        assert "test" in results[0]["line_content"]
        assert len(results[0]["match_positions"]) >= 1
        
        # Check second result
        assert results[1]["line_number"] == 3
        assert "test" in results[1]["line_content"]
        assert len(results[1]["match_positions"]) >= 1
    
    def test_search_in_large_file(self, temp_dir):
        """Test searching in large file that uses mmap"""
        # Create large file with known search terms
        lines = []
        for i in range(1000):
            if i % 100 == 0:
                lines.append(f"Line {i} contains searchterm\n")
            else:
                lines.append(f"Line {i} regular content\n")
        
        content = "".join(lines)
        
        file_path = os.path.join(temp_dir, "search_large.txt")
        with open(file_path, "w", encoding='utf-8') as f:
            f.write(content)
        
        results = MMapFileHandler.search_in_file(file_path, "searchterm", max_results=5)
        
        assert len(results) <= 5, "Should respect max_results limit"
        assert len(results) > 0, "Should find at least one match"
        
        # All results should contain the search term
        for result in results:
            assert "searchterm" in result["line_content"]
            assert result["line_number"] > 0
            assert len(result["match_positions"]) > 0
            assert isinstance(result["match_positions"], list)
    
    def test_search_no_matches(self, temp_dir):
        """Test searching with no matches"""
        content = "Line 1\nLine 2\nLine 3\n"
        
        file_path = os.path.join(temp_dir, "no_matches.txt")
        with open(file_path, "w", encoding='utf-8') as f:
            f.write(content)
        
        results = MMapFileHandler.search_in_file(file_path, "nonexistent")
        assert results == [], "Should return empty list when no matches"
    
    def test_search_multiple_matches_per_line(self, temp_dir):
        """Test searching with multiple matches per line"""
        content = "test line with test and test again\n"
        
        file_path = os.path.join(temp_dir, "multiple_matches.txt")
        with open(file_path, "w", encoding='utf-8') as f:
            f.write(content)
        
        results = MMapFileHandler.search_in_file(file_path, "test")
        
        assert len(results) == 1, "Should have one line result"
        assert len(results[0]["match_positions"]) == 3, "Should find 3 occurrences of 'test'"
        
        # Check that positions are correct
        positions = results[0]["match_positions"]
        line_content = results[0]["line_content"]
        for pos in positions:
            assert line_content[pos:pos+4] == "test", f"Position {pos} should contain 'test'"
    
    def test_search_case_sensitive(self, temp_dir):
        """Test that search is case sensitive"""
        content = "Line with Test\nLine with test\nLine with TEST\n"
        
        file_path = os.path.join(temp_dir, "case_test.txt")
        with open(file_path, "w", encoding='utf-8') as f:
            f.write(content)
        
        results = MMapFileHandler.search_in_file(file_path, "test")
        
        # Should only match lowercase "test"
        assert len(results) == 1, "Should find only lowercase 'test'"
        assert results[0]["line_number"] == 2
    
    def test_search_max_results_limit(self, temp_dir):
        """Test max_results parameter"""
        lines = [f"Line {i} with test\n" for i in range(100)]
        content = "".join(lines)
        
        file_path = os.path.join(temp_dir, "many_matches.txt")
        with open(file_path, "w", encoding='utf-8') as f:
            f.write(content)
        
        results = MMapFileHandler.search_in_file(file_path, "test", max_results=10)
        assert len(results) == 10, "Should respect max_results=10"
        
        # Test with no limit (or very high limit)
        results_unlimited = MMapFileHandler.search_in_file(file_path, "test", max_results=1000)
        assert len(results_unlimited) == 100, "Should find all matches when limit is high"
    
    def test_search_empty_search_term(self, temp_dir):
        """Test searching with empty search term"""
        content = "Line 1\nLine 2\nLine 3\n"
        
        file_path = os.path.join(temp_dir, "empty_search.txt")
        with open(file_path, "w", encoding='utf-8') as f:
            f.write(content)
        
        results = MMapFileHandler.search_in_file(file_path, "")
        
        # Empty search should return no results or handle gracefully
        assert isinstance(results, list), "Should return a list"
    
    def test_search_unicode_content(self, temp_dir):
        """Test searching in file with Unicode content"""
        content = "Line with émojis 🚀 and test content\nAnother line with tëst\nLine with тест\n"
        
        file_path = os.path.join(temp_dir, "unicode_search.txt")
        with open(file_path, "w", encoding='utf-8') as f:
            f.write(content)
        
        # Search for ASCII term
        results = MMapFileHandler.search_in_file(file_path, "test")
        assert len(results) >= 1, "Should find ASCII 'test'"
        
        # Search for Unicode term
        results_unicode = MMapFileHandler.search_in_file(file_path, "🚀")
        # Should handle Unicode search gracefully (may or may not find matches)
        assert isinstance(results_unicode, list), "Should handle Unicode search"
    
    def test_search_binary_file_handling(self, temp_dir):
        """Test searching in file with binary content"""
        # Create a file with some binary content
        binary_content = b'\x00\x01\x02\x03test\x04\x05\x06\x07\nmore test data\n'
        
        file_path = os.path.join(temp_dir, "binary_file.dat")
        with open(file_path, "wb") as f:
            f.write(binary_content)
        
        # Should not raise exception, might find or not find matches
        try:
            results = MMapFileHandler.search_in_file(file_path, "test")
            assert isinstance(results, list), "Should return list even for binary files"
        except UnicodeDecodeError:
            # Acceptable if function doesn't handle binary files
            pass
    
    def test_search_very_long_lines(self, temp_dir):
        """Test searching in file with very long lines"""
        # Create a file with one very long line
        long_line = "start " + "middle " * 10000 + "test " + "end " * 1000 + "\n"
        content = long_line + "short line\n"
        
        file_path = os.path.join(temp_dir, "long_lines.txt")
        with open(file_path, "w", encoding='utf-8') as f:
            f.write(content)
        
        results = MMapFileHandler.search_in_file(file_path, "test")
        
        assert len(results) >= 1, "Should find match in long line"
        assert results[0]["line_number"] == 1, "Should be in first line"
        assert len(results[0]["match_positions"]) >= 1, "Should find position"
    
    def test_search_special_characters(self, temp_dir):
        """Test searching for special characters and patterns"""
        content = "Line with dots...\nLine with [brackets]\nLine with (parentheses)\nLine with $pecial chars!\n"
        
        file_path = os.path.join(temp_dir, "special_chars.txt")
        with open(file_path, "w", encoding='utf-8') as f:
            f.write(content)
        
        test_cases = [
            ("...", 1),  # Dots
            ("[brackets]", 1),  # Brackets
            ("(parentheses)", 1),  # Parentheses
            ("$pecial", 1),  # Special characters
        ]
        
        for search_term, expected_count in test_cases:
            results = MMapFileHandler.search_in_file(file_path, search_term)
            assert len(results) >= expected_count, f"Should find '{search_term}'"


@pytest.mark.integration
@pytest.mark.skipif(not AIRD_AVAILABLE, reason="aird.main module not available")
class TestMMapHandlerIntegration:
    """Integration tests for MMapFileHandler methods working together"""
    
    @pytest.mark.asyncio
    async def test_file_serving_and_search_integration(self, temp_dir):
        """Test that file serving and search work together"""
        # Create a test file
        content = "Line 1: Hello world\nLine 2: Python testing\nLine 3: Search test\nLine 4: Final line\n"
        
        file_path = os.path.join(temp_dir, "integration_test.txt")
        # Write in binary mode to ensure consistent line endings
        with open(file_path, "wb") as f:
            f.write(content.encode('utf-8'))
        
        # Test file serving
        chunks = []
        async for chunk in MMapFileHandler.serve_file_chunk(file_path):
            chunks.append(chunk)
        
        served_content = b''.join(chunks).decode('utf-8')
        assert served_content == content, "Served content should match original"
        
        # Test search
        search_results = MMapFileHandler.search_in_file(file_path, "test")
        assert len(search_results) == 2, "Should find 'test' in lines 2 and 3"
        
        # Test line offsets
        offsets = MMapFileHandler.find_line_offsets(file_path)
        assert len(offsets) == 4, "Should find 4 line offsets"
        assert offsets[0] == 0, "First line should start at 0"
    
    @pytest.mark.asyncio
    async def test_large_file_operations(self, temp_dir):
        """Test all operations on a large file that uses mmap"""
        # Create a large file that will trigger mmap
        # Calculate lines needed to exceed MMAP_MIN_SIZE
        avg_line_length = 50  # Rough estimate
        min_lines_needed = (MMAP_MIN_SIZE // avg_line_length) + 1000  # Extra to be safe
        
        lines = []
        for i in range(min_lines_needed):
            if i % 500 == 0:
                lines.append(f"Line {i}: Special searchable content here\n")
            else:
                lines.append(f"Line {i}: Regular content for line number {i}\n")
        
        content = "".join(lines)
        
        file_path = os.path.join(temp_dir, "large_integration.txt")
        # Write in binary mode to ensure consistent handling
        with open(file_path, "wb") as f:
            f.write(content.encode('utf-8'))
        
        # Verify file is large enough to use mmap
        file_size = os.path.getsize(file_path)
        assert MMapFileHandler.should_use_mmap(file_size), f"File should be large enough for mmap. Size: {file_size}, Min: {MMAP_MIN_SIZE}"
        
        # Test partial file serving
        chunks = []
        async for chunk in MMapFileHandler.serve_file_chunk(file_path, start=1000, end=2000):
            chunks.append(chunk)
        
        partial_content = b''.join(chunks)
        assert len(partial_content) == 1001, "Should serve exactly 1001 bytes"
        
        # Test search with limited results
        search_results = MMapFileHandler.search_in_file(file_path, "searchable", max_results=5)
        assert len(search_results) == 5, "Should find exactly 5 results"
        
        # Test line offsets with limit
        offsets = MMapFileHandler.find_line_offsets(file_path, max_lines=100)
        assert len(offsets) <= 100, "Should respect line limit"
        assert len(offsets) > 0, "Should find some lines"
    
    def test_error_handling_consistency(self, temp_dir):
        """Test that all methods handle errors consistently"""
        nonexistent_file = os.path.join(temp_dir, "nonexistent.txt")
        
        # All methods should handle missing files gracefully
        with pytest.raises(FileNotFoundError):
            MMapFileHandler.find_line_offsets(nonexistent_file)
        
        with pytest.raises(FileNotFoundError):
            MMapFileHandler.search_in_file(nonexistent_file, "test")
        
        # serve_file_chunk is async, so test separately
        async def test_serve_nonexistent():
            with pytest.raises(FileNotFoundError):
                async for chunk in MMapFileHandler.serve_file_chunk(nonexistent_file):
                    pass
        
        # Run the async test
        asyncio.run(test_serve_nonexistent())