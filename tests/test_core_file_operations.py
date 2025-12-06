import pytest
import os
import shutil
from unittest.mock import patch, MagicMock, ANY
from aird.core.file_operations import (
    get_all_files_recursive,
    matches_glob_patterns,
    filter_files_by_patterns,
    cloud_root_dir,
    ensure_share_cloud_dir,
    sanitize_cloud_filename,
    is_cloud_relative_path,
    remove_cloud_file_if_exists,
    cleanup_share_cloud_dir_if_empty,
    remove_share_cloud_dir,
    download_cloud_item,
    download_cloud_items,
    configure_cloud_providers,
)
from aird.constants import ROOT_DIR, CLOUD_SHARE_FOLDER
from aird.cloud import CloudProviderError

class TestFileOperations:
    
    # --- get_all_files_recursive ---
    def test_get_all_files_recursive_simple(self):
        with patch('os.listdir', return_value=['file1.txt', 'dir1']), \
             patch('os.path.isfile', side_effect=lambda p: 'file1.txt' in p), \
             patch('os.path.isdir', side_effect=lambda p: 'dir1' in p), \
             patch('aird.core.file_operations.get_all_files_recursive', side_effect=[['dir1/file2.txt']]) as mock_recursive:
             
             # We need to mock the recursive call manually if we patch the function itself, 
             # but here we want to test the logic. 
             # Better approach: mock os.walk-like behavior or just listdir/isfile/isdir
             pass

    def test_get_all_files_recursive_logic(self):
        # Mock file system structure:
        # root/
        #   file1.txt
        #   dir1/
        #     file2.txt
        
        def normalize(path):
            return path.replace('\\', '/')
        
        def mock_listdir(path):
            normalized = normalize(path)
            if normalized == '/root':
                return ['file1.txt', 'dir1']
            if normalized == '/root/dir1':
                return ['file2.txt']
            return []

        def mock_isfile(path):
            return path.endswith('.txt')

        def mock_isdir(path):
            return 'dir1' in path and not path.endswith('.txt')

        with patch('os.listdir', side_effect=mock_listdir), \
             patch('os.path.isfile', side_effect=mock_isfile), \
             patch('os.path.isdir', side_effect=mock_isdir), \
             patch('os.path.join', side_effect=os.path.join):
            
            files = get_all_files_recursive('/root')
            assert 'file1.txt' in files
            # Note: os.path.join might produce backslashes on Windows, normalize for check
            normalized_files = [f.replace('\\', '/') for f in files]
            assert 'dir1/file2.txt' in normalized_files

    def test_get_all_files_recursive_error(self):
        with patch('os.listdir', side_effect=PermissionError("Access denied")), \
             patch('builtins.print') as mock_print:
            files = get_all_files_recursive('/root')
            assert files == []
            mock_print.assert_called()

    # --- matches_glob_patterns ---
    def test_matches_glob_patterns(self):
        assert matches_glob_patterns("test.py", ["*.py"]) is True
        assert matches_glob_patterns("test.txt", ["*.py"]) is False
        assert matches_glob_patterns("test.py", []) is False
        assert matches_glob_patterns("path/to/test.py", ["*.py"]) is True

    # --- filter_files_by_patterns ---
    def test_filter_files_by_patterns(self):
        files = ["a.py", "b.txt", "c.py", "d.md"]
        
        # No filters
        assert filter_files_by_patterns(files) == files
        
        # Allow list only
        assert filter_files_by_patterns(files, allow_list=["*.py"]) == ["a.py", "c.py"]
        
        # Avoid list only
        assert filter_files_by_patterns(files, avoid_list=["*.txt"]) == ["a.py", "c.py", "d.md"]
        
        # Both
        assert filter_files_by_patterns(files, allow_list=["*.py", "*.txt"], avoid_list=["b.txt"]) == ["a.py", "c.py"]

    # --- Cloud Directory Helpers ---
    def test_cloud_root_dir(self):
        assert cloud_root_dir() == os.path.join(ROOT_DIR, CLOUD_SHARE_FOLDER)

    def test_ensure_share_cloud_dir(self):
        with patch('os.makedirs') as mock_makedirs:
            path = ensure_share_cloud_dir("share1")
            expected = os.path.join(ROOT_DIR, CLOUD_SHARE_FOLDER, "share1")
            assert path == expected
            mock_makedirs.assert_called_with(expected, exist_ok=True)

    def test_sanitize_cloud_filename(self):
        assert sanitize_cloud_filename("test.txt") == "test.txt"
        assert sanitize_cloud_filename("path/to/file.txt") == "path_to_file.txt"
        assert sanitize_cloud_filename("../../etc/passwd") == "etc_passwd"
        assert sanitize_cloud_filename(None) == "cloud_file"
        assert sanitize_cloud_filename("") == "cloud_file"
        # Test length limit
        long_name = "a" * 200
        assert len(sanitize_cloud_filename(long_name)) == 128

    def test_is_cloud_relative_path(self):
        share_id = "123"
        prefix = f"{CLOUD_SHARE_FOLDER}/{share_id}/"
        assert is_cloud_relative_path(share_id, f"{prefix}file.txt") is True
        assert is_cloud_relative_path(share_id, "other/file.txt") is False

    # --- Cloud File Removal ---
    def test_remove_cloud_file_if_exists(self):
        share_id = "123"
        rel_path = f"{CLOUD_SHARE_FOLDER}/{share_id}/file.txt"
        abs_path = os.path.join(ROOT_DIR, rel_path)
        
        with patch('aird.core.file_operations.is_within_root', return_value=True), \
             patch('os.path.isfile', return_value=True), \
             patch('os.remove') as mock_remove, \
             patch('aird.core.file_operations.cleanup_share_cloud_dir_if_empty') as mock_cleanup:
            
            remove_cloud_file_if_exists(share_id, rel_path)
            mock_remove.assert_called_with(os.path.abspath(abs_path))
            mock_cleanup.assert_called_with(share_id)

    def test_remove_cloud_file_invalid_path(self):
        remove_cloud_file_if_exists("123", "invalid/path")
        # Should return early, no mocks needed as they wouldn't be called

    def test_cleanup_share_cloud_dir_if_empty(self):
        with patch('os.path.isdir', return_value=True), \
             patch('os.listdir', return_value=[]), \
             patch('shutil.rmtree') as mock_rmtree:
            
            cleanup_share_cloud_dir_if_empty("123")
            mock_rmtree.assert_called()

    def test_cleanup_share_cloud_dir_not_empty(self):
        with patch('os.path.isdir', return_value=True), \
             patch('os.listdir', return_value=['file.txt']), \
             patch('shutil.rmtree') as mock_rmtree:
            
            cleanup_share_cloud_dir_if_empty("123")
            mock_rmtree.assert_not_called()

    def test_remove_share_cloud_dir(self):
        with patch('shutil.rmtree') as mock_rmtree:
            remove_share_cloud_dir("123")
            mock_rmtree.assert_called()

    # --- Download Cloud Item ---
    def test_download_cloud_item_success(self):
        item = {"provider": "gd", "id": "file1", "name": "test.txt"}
        mock_provider = MagicMock()
        mock_download = MagicMock()
        mock_download.iter_chunks.return_value = [b"chunk"]
        mock_provider.download_file.return_value = mock_download
        
        with patch('aird.core.file_operations.CLOUD_MANAGER.get', return_value=mock_provider), \
             patch('aird.core.file_operations.ensure_share_cloud_dir', return_value='/tmp/share'), \
             patch('builtins.open', new_callable=MagicMock), \
             patch('os.path.exists', return_value=False), \
             patch('os.path.relpath', return_value='cloud/share/test.txt'):
            
            rel_path = download_cloud_item("share1", item)
            assert rel_path == 'cloud/share/test.txt'

    def test_download_cloud_item_errors(self):
        # Missing provider
        with pytest.raises(CloudProviderError, match="Invalid cloud file specification"):
            download_cloud_item("share1", {})

        # Folder not supported
        with pytest.raises(CloudProviderError, match="Cloud folder sharing is not supported"):
            download_cloud_item("share1", {"provider": "gd", "id": "1", "is_dir": True})

        # Provider not configured
        with patch('aird.core.file_operations.CLOUD_MANAGER.get', return_value=None):
            with pytest.raises(CloudProviderError, match="not configured"):
                download_cloud_item("share1", {"provider": "gd", "id": "1"})

    def test_download_cloud_items(self):
        with patch('aird.core.file_operations.download_cloud_item', side_effect=['path1', CloudProviderError("fail")]), \
             patch('builtins.print') as mock_print:
            
            paths = download_cloud_items("share1", [{}, {}])
            assert paths == ['path1']
            mock_print.assert_called()

    # --- Configure Cloud Providers ---
    def test_configure_cloud_providers(self):
        config = {
            "google_drive": {"enabled": True, "credentials_file": "creds.json"},
            "onedrive": {"enabled": True, "client_id": "id", "redirect_uri": "uri"}
        }
        
        with patch('aird.cloud.GoogleDriveProvider') as mock_gd, \
             patch('aird.cloud.OneDriveProvider') as mock_od, \
             patch('aird.core.file_operations.CLOUD_MANAGER') as mock_cm:
            
            configure_cloud_providers(config)
            assert mock_cm.register.call_count == 2
            mock_gd.assert_called()
            mock_od.assert_called()

    def test_configure_cloud_providers_missing_config(self):
        config = {
            "google_drive": {"enabled": True}, # Missing creds
            "onedrive": {"enabled": True} # Missing client_id
        }
        with patch('builtins.print') as mock_print:
            configure_cloud_providers(config)
            # Should print warnings
            assert mock_print.call_count >= 2
