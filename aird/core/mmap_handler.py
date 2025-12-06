"""Memory-mapped file handler for efficient large file operations."""

import os
import mmap
from datetime import datetime
from aird.constants import MMAP_MIN_SIZE, CHUNK_SIZE


class MMapFileHandler:
    """Efficient file handling using memory mapping for large files"""
    
    @staticmethod
    def should_use_mmap(file_size: int) -> bool:
        """Determine if mmap should be used based on file size"""
        return file_size >= MMAP_MIN_SIZE
    
    @staticmethod
    async def serve_file_chunk(file_path: str, start: int = 0, end: int = None, chunk_size: int = CHUNK_SIZE):
        """Serve file chunks using mmap for efficient memory usage"""
        try:
            file_size = os.path.getsize(file_path)
            
            if not MMapFileHandler.should_use_mmap(file_size):
                # Use traditional method for small files
                with open(file_path, 'rb') as f:
                    f.seek(start)
                    remaining = (end - start + 1) if end is not None else file_size - start
                    while remaining > 0:
                        chunk = f.read(min(chunk_size, remaining))
                        if not chunk:
                            break
                        yield chunk
                        remaining -= len(chunk)
                return
            
            # Use mmap for large files
            with open(file_path, 'rb') as f:
                with mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ) as mm:
                    actual_end = min(end or file_size - 1, file_size - 1)
                    current = start
                    
                    while current <= actual_end:
                        chunk_end = min(current + chunk_size, actual_end + 1)
                        yield mm[current:chunk_end]
                        current = chunk_end
                        
        except (OSError, ValueError) as e:
            # Fallback to traditional method on mmap errors
            with open(file_path, 'rb') as f:
                f.seek(start)
                remaining = (end - start + 1) if end is not None else file_size - start
                while remaining > 0:
                    chunk = f.read(min(chunk_size, remaining))
                    if not chunk:
                        break
                    yield chunk
                    remaining -= len(chunk)
    
    @staticmethod
    def find_line_offsets(file_path: str, max_lines: int = None) -> list[int]:
        """Efficiently find line start offsets using mmap"""
        try:
            file_size = os.path.getsize(file_path)
            if not MMapFileHandler.should_use_mmap(file_size):
                # Use traditional method for small files
                offsets = [0]
                with open(file_path, 'rb') as f:
                    pos = 0
                    for line in f:
                        pos += len(line)
                        offsets.append(pos)
                        if max_lines and len(offsets) > max_lines:
                            break
                return offsets[:-1]  # Remove the last offset (EOF)
            
            # Use mmap for large files
            offsets = [0]
            with open(file_path, 'rb') as f:
                with mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ) as mm:
                    pos = 0
                    while pos < len(mm):
                        newline_pos = mm.find(b'\n', pos)
                        if newline_pos == -1:
                            break
                        pos = newline_pos + 1
                        offsets.append(pos)
                        if max_lines and len(offsets) > max_lines:
                            break
            return offsets[:-1]
            
        except (OSError, ValueError):
            # Fallback to traditional method
            offsets = [0]
            with open(file_path, 'rb') as f:
                pos = 0
                for line in f:
                    pos += len(line)
                    offsets.append(pos)
                    if max_lines and len(offsets) > max_lines:
                        break
            return offsets[:-1]
    
    @staticmethod
    def search_in_file(file_path: str, search_term: str, max_results: int = 100) -> list[dict]:
        """Efficiently search for text in file using mmap"""
        results = []
        try:
            file_size = os.path.getsize(file_path)
            search_bytes = search_term.encode('utf-8')
            
            if not MMapFileHandler.should_use_mmap(file_size):
                # Use traditional method for small files
                with open(file_path, 'r', encoding='utf-8', errors='replace') as f:
                    for line_num, line in enumerate(f, 1):
                        if search_term in line:
                            results.append({
                                "line_number": line_num,
                                "line_content": line.rstrip('\n'),
                                "match_positions": [i for i in range(len(line)) if line[i:].startswith(search_term)]
                            })
                            if len(results) >= max_results:
                                break
                return results
            
            # Use mmap for large files
            with open(file_path, 'rb') as f:
                with mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ) as mm:
                    current_pos = 0
                    line_number = 1
                    line_start = 0
                    
                    while current_pos < len(mm) and len(results) < max_results:
                        newline_pos = mm.find(b'\n', current_pos)
                        if newline_pos == -1:
                            # Last line
                            line_bytes = mm[current_pos:]
                            if search_bytes in line_bytes:
                                line_content = line_bytes.decode('utf-8', errors='replace')
                                match_positions = []
                                start_pos = 0
                                while True:
                                    pos = line_content.find(search_term, start_pos)
                                    if pos == -1:
                                        break
                                    match_positions.append(pos)
                                    start_pos = pos + 1
                                results.append({
                                    "line_number": line_number,
                                    "line_content": line_content,
                                    "match_positions": match_positions
                                })
                            break
                        
                        line_bytes = mm[current_pos:newline_pos]
                        if search_bytes in line_bytes:
                            line_content = line_bytes.decode('utf-8', errors='replace')
                            match_positions = []
                            start_pos = 0
                            while True:
                                pos = line_content.find(search_term, start_pos)
                                if pos == -1:
                                    break
                                match_positions.append(pos)
                                start_pos = pos + 1
                            results.append({
                                "line_number": line_number,
                                "line_content": line_content,
                                "match_positions": match_positions
                            })
                        
                        current_pos = newline_pos + 1
                        line_number += 1
                        
        except (OSError, UnicodeDecodeError):
            # Fallback to traditional search
            with open(file_path, 'r', encoding='utf-8', errors='replace') as f:
                for line_num, line in enumerate(f, 1):
                    if search_term in line:
                        results.append({
                            "line_number": line_num,
                            "line_content": line.rstrip('\n'),
                            "match_positions": [i for i in range(len(line)) if line[i:].startswith(search_term)]
                        })
                        if len(results) >= max_results:
                            break
        
        return results


def get_files_in_directory(path="."):
    """Get all files in a directory with metadata."""
    files = []
    for entry in os.scandir(path):
        stat = entry.stat()
        files.append({
            "name": entry.name,
            "is_dir": entry.is_dir(),
            "size_bytes": stat.st_size,
            "size_str": f"{stat.st_size / 1024:.2f} KB" if not entry.is_dir() else "-",
            "modified": datetime.fromtimestamp(stat.st_mtime).strftime('%Y-%m-%d %H:%M:%S'),
            "modified_timestamp": int(stat.st_mtime)
        })
    return files


def is_video_file(filename):
    """Check if file is a supported video format"""
    ext = os.path.splitext(filename)[1].lower()
    video_extensions = {'.mp4', '.avi', '.mkv', '.mov', '.wmv', '.flv', '.webm', '.m4v', '.3gp', '.ogv'}
    return ext in video_extensions


def is_audio_file(filename):
    """Check if file is a supported audio format"""
    ext = os.path.splitext(filename)[1].lower()
    audio_extensions = {'.mp3', '.wav', '.flac', '.aac', '.ogg', '.m4a', '.wma'}
    return ext in audio_extensions
