"""Tests for aird.cloud module."""

import pytest
from unittest.mock import MagicMock, patch
from io import BytesIO

from aird.cloud import (
    CloudProviderError,
    CloudFile,
    CloudDownload,
    CloudProvider,
    CloudManager,
    GoogleDriveProvider,
    OneDriveProvider,
    encode_identifier,
    decode_identifier,
    _safe_int,
)


class TestCloudProviderError:
    def test_exception_message(self):
        error = CloudProviderError("Something went wrong")
        assert str(error) == "Something went wrong"

    def test_exception_inheritance(self):
        error = CloudProviderError("Test")
        assert isinstance(error, Exception)


class TestCloudFile:
    def test_basic_creation(self):
        cloud_file = CloudFile(
            id="123",
            name="test.txt",
            is_dir=False,
            size=1024,
            modified="2024-01-01T00:00:00Z"
        )
        assert cloud_file.id == "123"
        assert cloud_file.name == "test.txt"
        assert cloud_file.is_dir is False
        assert cloud_file.size == 1024
        assert cloud_file.modified == "2024-01-01T00:00:00Z"

    def test_creation_with_defaults(self):
        cloud_file = CloudFile(id="456", name="folder", is_dir=True)
        assert cloud_file.id == "456"
        assert cloud_file.name == "folder"
        assert cloud_file.is_dir is True
        assert cloud_file.size is None
        assert cloud_file.modified is None

    def test_to_dict(self):
        cloud_file = CloudFile(
            id="789",
            name="doc.pdf",
            is_dir=False,
            size=2048,
            modified="2024-06-15T12:30:00Z"
        )
        result = cloud_file.to_dict()
        assert result == {
            "id": "789",
            "name": "doc.pdf",
            "is_dir": False,
            "size": 2048,
            "modified": "2024-06-15T12:30:00Z"
        }

    def test_to_dict_with_none_values(self):
        cloud_file = CloudFile(id="abc", name="file", is_dir=False)
        result = cloud_file.to_dict()
        assert result["size"] is None
        assert result["modified"] is None


class TestCloudDownload:
    def test_initialization_with_headers(self):
        mock_response = MagicMock()
        mock_response.headers = {
            "Content-Type": "application/pdf",
            "Content-Length": "1024"
        }
        
        download = CloudDownload("test.pdf", mock_response)
        
        assert download.name == "test.pdf"
        assert download.content_type == "application/pdf"
        assert download.content_length == 1024

    def test_initialization_with_explicit_values(self):
        mock_response = MagicMock()
        mock_response.headers = {}
        
        download = CloudDownload(
            "test.pdf",
            mock_response,
            content_type="application/octet-stream",
            content_length=2048
        )
        
        assert download.content_type == "application/octet-stream"
        assert download.content_length == 2048

    def test_initialization_invalid_content_length(self):
        mock_response = MagicMock()
        mock_response.headers = {"Content-Length": "invalid"}
        
        download = CloudDownload("test.pdf", mock_response)
        assert download.content_length is None

    def test_initialization_none_content_length(self):
        mock_response = MagicMock()
        mock_response.headers = {}
        
        download = CloudDownload("test.pdf", mock_response)
        assert download.content_length is None

    def test_iter_chunks(self):
        mock_response = MagicMock()
        mock_response.iter_content.return_value = [b"chunk1", b"chunk2", b"", b"chunk3"]
        mock_response.headers = {}
        
        download = CloudDownload("test.pdf", mock_response)
        chunks = list(download.iter_chunks(chunk_size=1024))
        
        assert chunks == [b"chunk1", b"chunk2", b"chunk3"]
        mock_response.iter_content.assert_called_with(chunk_size=1024)

    def test_close(self):
        mock_response = MagicMock()
        mock_response.headers = {}
        
        download = CloudDownload("test.pdf", mock_response)
        download.close()
        
        mock_response.close.assert_called_once()


class TestCloudProvider:
    def test_metadata(self):
        provider = CloudProvider()
        provider.name = "test_provider"
        provider.label = "Test Provider"
        
        result = provider.metadata()
        assert result == {"name": "test_provider", "label": "Test Provider"}

    def test_root_identifier_default(self):
        provider = CloudProvider()
        assert provider.root_identifier == "root"

    def test_list_files_not_implemented(self):
        provider = CloudProvider()
        with pytest.raises(NotImplementedError):
            provider.list_files()

    def test_download_file_not_implemented(self):
        provider = CloudProvider()
        with pytest.raises(NotImplementedError):
            provider.download_file("file_id")

    def test_upload_file_not_implemented(self):
        provider = CloudProvider()
        stream = BytesIO(b"test")
        with pytest.raises(NotImplementedError):
            provider.upload_file(stream, name="test.txt")


class TestCloudManager:
    def test_initialization(self):
        manager = CloudManager()
        assert manager.list_providers() == []
        assert not manager.has_providers()

    def test_register_provider(self):
        manager = CloudManager()
        mock_provider = MagicMock()
        mock_provider.name = "test"
        
        manager.register(mock_provider)
        
        assert manager.has_providers()
        assert manager.get("test") == mock_provider

    def test_register_provider_no_name(self):
        manager = CloudManager()
        mock_provider = MagicMock()
        mock_provider.name = ""
        
        with pytest.raises(ValueError, match="Provider must define a name"):
            manager.register(mock_provider)

    def test_register_none_provider(self):
        manager = CloudManager()
        
        with pytest.raises(ValueError, match="Provider must define a name"):
            manager.register(None)

    def test_get_nonexistent_provider(self):
        manager = CloudManager()
        assert manager.get("nonexistent") is None

    def test_list_providers(self):
        manager = CloudManager()
        provider1 = MagicMock()
        provider1.name = "provider1"
        provider2 = MagicMock()
        provider2.name = "provider2"
        
        manager.register(provider1)
        manager.register(provider2)
        
        providers = manager.list_providers()
        assert len(providers) == 2
        assert provider1 in providers
        assert provider2 in providers

    def test_reset(self):
        manager = CloudManager()
        mock_provider = MagicMock()
        mock_provider.name = "test"
        
        manager.register(mock_provider)
        assert manager.has_providers()
        
        manager.reset()
        assert not manager.has_providers()
        assert manager.get("test") is None


class TestGoogleDriveProvider:
    def test_initialization(self):
        provider = GoogleDriveProvider("test_token")
        assert provider.name == "gdrive"
        assert provider.label == "Google Drive"
        assert provider.root_identifier == "root"

    def test_initialization_with_root_id(self):
        provider = GoogleDriveProvider("test_token", root_id="custom_root")
        assert provider.root_identifier == "custom_root"

    def test_initialization_empty_root_id(self):
        provider = GoogleDriveProvider("test_token", root_id="")
        assert provider.root_identifier == "root"

    def test_initialization_no_token(self):
        with pytest.raises(CloudProviderError, match="access token is required"):
            GoogleDriveProvider("")

    def test_headers(self):
        provider = GoogleDriveProvider("my_token")
        headers = provider._headers()
        assert headers["Authorization"] == "Bearer my_token"
        assert headers["Accept"] == "application/json"

    @patch("aird.cloud.requests.get")
    def test_list_files_success(self, mock_get):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "files": [
                {"id": "1", "name": "file.txt", "mimeType": "text/plain", "size": "100"},
                {"id": "2", "name": "folder", "mimeType": "application/vnd.google-apps.folder"},
            ]
        }
        mock_get.return_value = mock_response
        
        provider = GoogleDriveProvider("test_token")
        files = provider.list_files()
        
        assert len(files) == 2
        assert files[0].name == "file.txt"
        assert files[0].is_dir is False
        assert files[0].size == 100
        assert files[1].name == "folder"
        assert files[1].is_dir is True

    @patch("aird.cloud.requests.get")
    def test_list_files_with_folder_id(self, mock_get):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"files": []}
        mock_get.return_value = mock_response
        
        provider = GoogleDriveProvider("test_token")
        provider.list_files("custom_folder_id")
        
        call_args = mock_get.call_args
        assert "'custom_folder_id' in parents" in call_args[1]["params"]["q"]

    @patch("aird.cloud.requests.get")
    def test_list_files_without_shared_drives(self, mock_get):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"files": []}
        mock_get.return_value = mock_response
        
        provider = GoogleDriveProvider("test_token", include_shared_drives=False)
        provider.list_files()
        
        call_args = mock_get.call_args
        assert "corpora" not in call_args[1]["params"]

    @patch("aird.cloud.requests.get")
    def test_list_files_request_error(self, mock_get):
        import requests
        mock_get.side_effect = requests.RequestException("Network error")
        
        provider = GoogleDriveProvider("test_token")
        with pytest.raises(CloudProviderError, match="request failed"):
            provider.list_files()

    @patch("aird.cloud.requests.get")
    def test_list_files_api_error(self, mock_get):
        mock_response = MagicMock()
        mock_response.status_code = 401
        mock_response.text = "Unauthorized"
        mock_get.return_value = mock_response
        
        provider = GoogleDriveProvider("test_token")
        with pytest.raises(CloudProviderError, match="list failed"):
            provider.list_files()

    @patch("aird.cloud.requests.get")
    def test_download_file_success(self, mock_get):
        mock_meta_response = MagicMock()
        mock_meta_response.status_code = 200
        mock_meta_response.json.return_value = {
            "id": "123",
            "name": "document.pdf",
            "mimeType": "application/pdf",
            "size": "1024"
        }
        
        mock_download_response = MagicMock()
        mock_download_response.status_code = 200
        mock_download_response.headers = {"Content-Type": "application/pdf"}
        
        mock_get.side_effect = [mock_meta_response, mock_download_response]
        
        provider = GoogleDriveProvider("test_token")
        download = provider.download_file("123")
        
        assert download.name == "document.pdf"
        assert download.content_length == 1024

    @patch("aird.cloud.requests.get")
    def test_download_file_folder_error(self, mock_get):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "id": "123",
            "name": "folder",
            "mimeType": "application/vnd.google-apps.folder"
        }
        mock_get.return_value = mock_response
        
        provider = GoogleDriveProvider("test_token")
        with pytest.raises(CloudProviderError, match="Folders cannot be downloaded"):
            provider.download_file("123")

    @patch("aird.cloud.requests.get")
    def test_download_file_google_docs_error(self, mock_get):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "id": "123",
            "name": "doc",
            "mimeType": "application/vnd.google-apps.document"
        }
        mock_get.return_value = mock_response
        
        provider = GoogleDriveProvider("test_token")
        with pytest.raises(CloudProviderError, match="Google Docs formats"):
            provider.download_file("123")

    @patch("aird.cloud.requests.get")
    def test_download_file_metadata_error(self, mock_get):
        mock_response = MagicMock()
        mock_response.status_code = 404
        mock_get.return_value = mock_response
        
        provider = GoogleDriveProvider("test_token")
        with pytest.raises(CloudProviderError, match="metadata fetch failed"):
            provider.download_file("123")

    @patch("aird.cloud.requests.get")
    def test_download_file_download_error(self, mock_get):
        mock_meta_response = MagicMock()
        mock_meta_response.status_code = 200
        mock_meta_response.json.return_value = {
            "id": "123",
            "name": "file.txt",
            "mimeType": "text/plain"
        }
        
        mock_download_response = MagicMock()
        mock_download_response.status_code = 500
        
        mock_get.side_effect = [mock_meta_response, mock_download_response]
        
        provider = GoogleDriveProvider("test_token")
        with pytest.raises(CloudProviderError, match="download failed"):
            provider.download_file("123")

    @patch("aird.cloud.requests.get")
    def test_download_file_request_exception(self, mock_get):
        import requests
        mock_get.side_effect = requests.RequestException("Network error")
        
        provider = GoogleDriveProvider("test_token")
        with pytest.raises(CloudProviderError, match="request failed"):
            provider.download_file("123")

    @patch("aird.cloud.requests.post")
    def test_upload_file_simple_success(self, mock_post):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "id": "new_file_id",
            "name": "uploaded.txt",
            "size": "100"
        }
        mock_post.return_value = mock_response
        
        provider = GoogleDriveProvider("test_token")
        stream = BytesIO(b"test content")
        result = provider.upload_file(stream, name="uploaded.txt", size=12)
        
        assert result.id == "new_file_id"
        assert result.name == "uploaded.txt"

    @patch("aird.cloud.requests.post")
    def test_upload_file_no_name(self, mock_post):
        provider = GoogleDriveProvider("test_token")
        stream = BytesIO(b"test")
        with pytest.raises(CloudProviderError, match="file name is required"):
            provider.upload_file(stream, name="")

    @patch("aird.cloud.requests.post")
    def test_upload_file_negative_size(self, mock_post):
        provider = GoogleDriveProvider("test_token")
        stream = BytesIO(b"test")
        with pytest.raises(CloudProviderError, match="Invalid upload size"):
            provider.upload_file(stream, name="test.txt", size=-1)

    @patch("aird.cloud.requests.post")
    def test_upload_file_auto_detect_size(self, mock_post):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"id": "1", "name": "test.txt"}
        mock_post.return_value = mock_response
        
        provider = GoogleDriveProvider("test_token")
        stream = BytesIO(b"test content")
        provider.upload_file(stream, name="test.txt")  # size not provided
        
        mock_post.assert_called_once()

    @patch("aird.cloud.requests.post")
    def test_upload_file_with_parent_id(self, mock_post):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"id": "1", "name": "test.txt"}
        mock_post.return_value = mock_response
        
        provider = GoogleDriveProvider("test_token")
        stream = BytesIO(b"test")
        provider.upload_file(stream, name="test.txt", parent_id="parent123", size=4)
        
        mock_post.assert_called_once()

    @patch("aird.cloud.requests.post")
    def test_upload_file_request_error(self, mock_post):
        import requests
        mock_post.side_effect = requests.RequestException("Network error")
        
        provider = GoogleDriveProvider("test_token")
        stream = BytesIO(b"test")
        with pytest.raises(CloudProviderError, match="upload failed"):
            provider.upload_file(stream, name="test.txt", size=4)

    @patch("aird.cloud.requests.post")
    def test_upload_file_api_error(self, mock_post):
        mock_response = MagicMock()
        mock_response.status_code = 403
        mock_response.text = "Forbidden"
        mock_post.return_value = mock_response
        
        provider = GoogleDriveProvider("test_token")
        stream = BytesIO(b"test")
        with pytest.raises(CloudProviderError, match="upload failed"):
            provider.upload_file(stream, name="test.txt", size=4)

    @patch("aird.cloud.requests.put")
    @patch("aird.cloud.requests.post")
    def test_upload_file_resumable_success(self, mock_post, mock_put):
        # Large file that requires resumable upload
        large_content = b"x" * (6 * 1024 * 1024)  # 6MB
        
        mock_init_response = MagicMock()
        mock_init_response.status_code = 200
        mock_init_response.headers = {"Location": "https://upload.url"}
        mock_post.return_value = mock_init_response
        
        mock_upload_response = MagicMock()
        mock_upload_response.status_code = 200
        mock_upload_response.json.return_value = {
            "id": "large_file_id",
            "name": "large.bin"
        }
        mock_put.return_value = mock_upload_response
        
        provider = GoogleDriveProvider("test_token")
        stream = BytesIO(large_content)
        result = provider.upload_file(stream, name="large.bin", size=len(large_content))
        
        assert result.id == "large_file_id"

    @patch("aird.cloud.requests.post")
    def test_upload_file_resumable_init_error(self, mock_post):
        large_content = b"x" * (6 * 1024 * 1024)
        
        mock_response = MagicMock()
        mock_response.status_code = 500
        mock_response.text = "Server error"
        mock_post.return_value = mock_response
        
        provider = GoogleDriveProvider("test_token")
        stream = BytesIO(large_content)
        with pytest.raises(CloudProviderError, match="upload init failed"):
            provider.upload_file(stream, name="large.bin", size=len(large_content))

    @patch("aird.cloud.requests.post")
    def test_upload_file_resumable_no_location(self, mock_post):
        large_content = b"x" * (6 * 1024 * 1024)
        
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.headers = {}  # No Location header
        mock_post.return_value = mock_response
        
        provider = GoogleDriveProvider("test_token")
        stream = BytesIO(large_content)
        with pytest.raises(CloudProviderError, match="did not provide an upload URL"):
            provider.upload_file(stream, name="large.bin", size=len(large_content))

    @patch("aird.cloud.requests.put")
    @patch("aird.cloud.requests.post")
    def test_upload_file_resumable_chunk_308(self, mock_post, mock_put):
        """Test resumable upload with 308 (Resume Incomplete) responses"""
        large_content = b"x" * (6 * 1024 * 1024)
        
        mock_init_response = MagicMock()
        mock_init_response.status_code = 200
        mock_init_response.headers = {"Location": "https://upload.url"}
        mock_post.return_value = mock_init_response
        
        # First chunk returns 308, second returns 200
        mock_308 = MagicMock()
        mock_308.status_code = 308
        
        mock_200 = MagicMock()
        mock_200.status_code = 200
        mock_200.json.return_value = {"id": "1", "name": "large.bin"}
        
        mock_put.side_effect = [mock_308, mock_200]
        
        provider = GoogleDriveProvider("test_token")
        stream = BytesIO(large_content)
        result = provider.upload_file(stream, name="large.bin", size=len(large_content))
        
        assert result.id == "1"

    @patch("aird.cloud.requests.put")
    @patch("aird.cloud.requests.post")
    def test_upload_file_resumable_chunk_error(self, mock_post, mock_put):
        large_content = b"x" * (6 * 1024 * 1024)
        
        mock_init_response = MagicMock()
        mock_init_response.status_code = 200
        mock_init_response.headers = {"Location": "https://upload.url"}
        mock_post.return_value = mock_init_response
        
        mock_error = MagicMock()
        mock_error.status_code = 500
        mock_error.text = "Server error"
        mock_put.return_value = mock_error
        
        provider = GoogleDriveProvider("test_token")
        stream = BytesIO(large_content)
        with pytest.raises(CloudProviderError, match="upload failed"):
            provider.upload_file(stream, name="large.bin", size=len(large_content))


class TestOneDriveProvider:
    def test_initialization(self):
        provider = OneDriveProvider("test_token")
        assert provider.name == "onedrive"
        assert provider.label == "OneDrive"

    def test_initialization_with_drive_id(self):
        provider = OneDriveProvider("test_token", drive_id="custom_drive")
        assert "drives/custom_drive" in provider._base_url

    def test_initialization_no_token(self):
        with pytest.raises(CloudProviderError, match="access token is required"):
            OneDriveProvider("")

    def test_headers(self):
        provider = OneDriveProvider("my_token")
        headers = provider._headers()
        assert headers["Authorization"] == "Bearer my_token"
        assert headers["Accept"] == "application/json"

    @patch("aird.cloud.requests.get")
    def test_list_files_success_root(self, mock_get):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "value": [
                {"id": "1", "name": "file.txt", "size": 100},
                {"id": "2", "name": "folder", "folder": {}},
            ]
        }
        mock_get.return_value = mock_response
        
        provider = OneDriveProvider("test_token")
        files = provider.list_files()
        
        assert len(files) == 2
        assert files[0].name == "file.txt"
        assert files[0].is_dir is False
        assert files[1].name == "folder"
        assert files[1].is_dir is True
        
        # Should use root URL
        call_url = mock_get.call_args[0][0]
        assert "/root/children" in call_url

    @patch("aird.cloud.requests.get")
    def test_list_files_with_folder_id(self, mock_get):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"value": []}
        mock_get.return_value = mock_response
        
        provider = OneDriveProvider("test_token")
        provider.list_files("custom_folder_id")
        
        call_url = mock_get.call_args[0][0]
        assert "/items/custom_folder_id/children" in call_url

    @patch("aird.cloud.requests.get")
    def test_list_files_request_error(self, mock_get):
        import requests
        mock_get.side_effect = requests.RequestException("Network error")
        
        provider = OneDriveProvider("test_token")
        with pytest.raises(CloudProviderError, match="request failed"):
            provider.list_files()

    @patch("aird.cloud.requests.get")
    def test_list_files_api_error(self, mock_get):
        mock_response = MagicMock()
        mock_response.status_code = 401
        mock_response.text = "Unauthorized"
        mock_get.return_value = mock_response
        
        provider = OneDriveProvider("test_token")
        with pytest.raises(CloudProviderError, match="list failed"):
            provider.list_files()

    @patch("aird.cloud.requests.get")
    def test_download_file_success(self, mock_get):
        mock_meta_response = MagicMock()
        mock_meta_response.status_code = 200
        mock_meta_response.json.return_value = {
            "name": "document.pdf",
            "size": 1024,
            "@microsoft.graph.downloadUrl": "https://download.url",
            "file": {}
        }
        
        mock_download_response = MagicMock()
        mock_download_response.status_code = 200
        mock_download_response.headers = {"Content-Type": "application/pdf"}
        
        mock_get.side_effect = [mock_meta_response, mock_download_response]
        
        provider = OneDriveProvider("test_token")
        download = provider.download_file("123")
        
        assert download.name == "document.pdf"
        assert download.content_length == 1024

    @patch("aird.cloud.requests.get")
    def test_download_file_folder_error(self, mock_get):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "name": "folder",
            "folder": {}
        }
        mock_get.return_value = mock_response
        
        provider = OneDriveProvider("test_token")
        with pytest.raises(CloudProviderError, match="Folders cannot be downloaded"):
            provider.download_file("123")

    @patch("aird.cloud.requests.get")
    def test_download_file_no_download_url(self, mock_get):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "name": "file.txt",
            "file": {}
            # No @microsoft.graph.downloadUrl
        }
        mock_get.return_value = mock_response
        
        provider = OneDriveProvider("test_token")
        with pytest.raises(CloudProviderError, match="Download URL not available"):
            provider.download_file("123")

    @patch("aird.cloud.requests.get")
    def test_download_file_metadata_error(self, mock_get):
        mock_response = MagicMock()
        mock_response.status_code = 404
        mock_get.return_value = mock_response
        
        provider = OneDriveProvider("test_token")
        with pytest.raises(CloudProviderError, match="metadata fetch failed"):
            provider.download_file("123")

    @patch("aird.cloud.requests.get")
    def test_download_file_download_error(self, mock_get):
        mock_meta_response = MagicMock()
        mock_meta_response.status_code = 200
        mock_meta_response.json.return_value = {
            "name": "file.txt",
            "@microsoft.graph.downloadUrl": "https://download.url",
            "file": {}
        }
        
        mock_download_response = MagicMock()
        mock_download_response.status_code = 500
        
        mock_get.side_effect = [mock_meta_response, mock_download_response]
        
        provider = OneDriveProvider("test_token")
        with pytest.raises(CloudProviderError, match="download failed"):
            provider.download_file("123")

    @patch("aird.cloud.requests.put")
    def test_upload_file_simple_success(self, mock_put):
        mock_response = MagicMock()
        mock_response.status_code = 201
        mock_response.json.return_value = {
            "id": "new_file_id",
            "name": "uploaded.txt",
            "size": 100
        }
        mock_put.return_value = mock_response
        
        provider = OneDriveProvider("test_token")
        stream = BytesIO(b"test content")
        result = provider.upload_file(stream, name="uploaded.txt", size=12)
        
        assert result.id == "new_file_id"
        assert result.name == "uploaded.txt"

    @patch("aird.cloud.requests.put")
    def test_upload_file_with_parent_id(self, mock_put):
        mock_response = MagicMock()
        mock_response.status_code = 201
        mock_response.json.return_value = {"id": "1", "name": "test.txt"}
        mock_put.return_value = mock_response
        
        provider = OneDriveProvider("test_token")
        stream = BytesIO(b"test")
        provider.upload_file(stream, name="test.txt", parent_id="parent123", size=4)
        
        call_url = mock_put.call_args[0][0]
        assert "/items/parent123:" in call_url

    @patch("aird.cloud.requests.put")
    def test_upload_file_no_name(self, mock_put):
        provider = OneDriveProvider("test_token")
        stream = BytesIO(b"test")
        with pytest.raises(CloudProviderError, match="file name is required"):
            provider.upload_file(stream, name="")

    @patch("aird.cloud.requests.put")
    def test_upload_file_negative_size(self, mock_put):
        provider = OneDriveProvider("test_token")
        stream = BytesIO(b"test")
        with pytest.raises(CloudProviderError, match="Invalid upload size"):
            provider.upload_file(stream, name="test.txt", size=-1)

    @patch("aird.cloud.requests.put")
    def test_upload_file_request_error(self, mock_put):
        import requests
        mock_put.side_effect = requests.RequestException("Network error")
        
        provider = OneDriveProvider("test_token")
        stream = BytesIO(b"test")
        with pytest.raises(CloudProviderError, match="upload failed"):
            provider.upload_file(stream, name="test.txt", size=4)

    @patch("aird.cloud.requests.put")
    def test_upload_file_api_error(self, mock_put):
        mock_response = MagicMock()
        mock_response.status_code = 403
        mock_response.text = "Forbidden"
        mock_put.return_value = mock_response
        
        provider = OneDriveProvider("test_token")
        stream = BytesIO(b"test")
        with pytest.raises(CloudProviderError, match="upload failed"):
            provider.upload_file(stream, name="test.txt", size=4)

    @patch("aird.cloud.requests.put")
    @patch("aird.cloud.requests.post")
    def test_upload_file_resumable_success(self, mock_post, mock_put):
        large_content = b"x" * (5 * 1024 * 1024)  # 5MB (over 4MB limit)
        
        mock_session_response = MagicMock()
        mock_session_response.status_code = 200
        mock_session_response.json.return_value = {"uploadUrl": "https://upload.url"}
        mock_post.return_value = mock_session_response
        
        mock_upload_response = MagicMock()
        mock_upload_response.status_code = 201
        mock_upload_response.json.return_value = {
            "id": "large_file_id",
            "name": "large.bin"
        }
        mock_put.return_value = mock_upload_response
        
        provider = OneDriveProvider("test_token")
        stream = BytesIO(large_content)
        result = provider.upload_file(stream, name="large.bin", size=len(large_content))
        
        assert result.id == "large_file_id"

    @patch("aird.cloud.requests.post")
    def test_upload_file_session_error(self, mock_post):
        large_content = b"x" * (5 * 1024 * 1024)
        
        mock_response = MagicMock()
        mock_response.status_code = 500
        mock_response.text = "Server error"
        mock_post.return_value = mock_response
        
        provider = OneDriveProvider("test_token")
        stream = BytesIO(large_content)
        with pytest.raises(CloudProviderError, match="session failed"):
            provider.upload_file(stream, name="large.bin", size=len(large_content))

    @patch("aird.cloud.requests.post")
    def test_upload_file_session_no_url(self, mock_post):
        large_content = b"x" * (5 * 1024 * 1024)
        
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {}  # No uploadUrl
        mock_post.return_value = mock_response
        
        provider = OneDriveProvider("test_token")
        stream = BytesIO(large_content)
        with pytest.raises(CloudProviderError, match="did not provide an upload URL"):
            provider.upload_file(stream, name="large.bin", size=len(large_content))

    @patch("aird.cloud.requests.put")
    @patch("aird.cloud.requests.post")
    def test_upload_file_resumable_chunk_202(self, mock_post, mock_put):
        """Test resumable upload with 202 (Accepted) responses"""
        large_content = b"x" * (5 * 1024 * 1024)
        
        mock_session_response = MagicMock()
        mock_session_response.status_code = 200
        mock_session_response.json.return_value = {"uploadUrl": "https://upload.url"}
        mock_post.return_value = mock_session_response
        
        # First chunk returns 202, second returns 201
        mock_202 = MagicMock()
        mock_202.status_code = 202
        
        mock_201 = MagicMock()
        mock_201.status_code = 201
        mock_201.json.return_value = {"id": "1", "name": "large.bin"}
        
        mock_put.side_effect = [mock_202, mock_201]
        
        provider = OneDriveProvider("test_token")
        stream = BytesIO(large_content)
        result = provider.upload_file(stream, name="large.bin", size=len(large_content))
        
        assert result.id == "1"

    @patch("aird.cloud.requests.put")
    @patch("aird.cloud.requests.post")
    def test_upload_file_resumable_chunk_error(self, mock_post, mock_put):
        large_content = b"x" * (5 * 1024 * 1024)
        
        mock_session_response = MagicMock()
        mock_session_response.status_code = 200
        mock_session_response.json.return_value = {"uploadUrl": "https://upload.url"}
        mock_post.return_value = mock_session_response
        
        mock_error = MagicMock()
        mock_error.status_code = 500
        mock_error.text = "Server error"
        mock_put.return_value = mock_error
        
        provider = OneDriveProvider("test_token")
        stream = BytesIO(large_content)
        with pytest.raises(CloudProviderError, match="upload failed"):
            provider.upload_file(stream, name="large.bin", size=len(large_content))


class TestEncodeDecode:
    def test_encode_identifier(self):
        result = encode_identifier("test/file/path")
        assert isinstance(result, str)
        assert "/" not in result  # Should be URL-safe
        assert "=" not in result  # Padding stripped

    def test_decode_identifier(self):
        encoded = encode_identifier("test/file/path")
        decoded = decode_identifier(encoded)
        assert decoded == "test/file/path"

    def test_roundtrip_simple(self):
        original = "simple_id"
        encoded = encode_identifier(original)
        decoded = decode_identifier(encoded)
        assert decoded == original

    def test_roundtrip_special_chars(self):
        original = "file/with spaces & special=chars!"
        encoded = encode_identifier(original)
        decoded = decode_identifier(encoded)
        assert decoded == original

    def test_roundtrip_unicode(self):
        original = "文件名/こんにちは/test"
        encoded = encode_identifier(original)
        decoded = decode_identifier(encoded)
        assert decoded == original

    def test_roundtrip_empty(self):
        original = ""
        encoded = encode_identifier(original)
        decoded = decode_identifier(encoded)
        assert decoded == original


class TestSafeInt:
    def test_valid_int(self):
        assert _safe_int(42) == 42

    def test_valid_string_int(self):
        assert _safe_int("100") == 100

    def test_none(self):
        assert _safe_int(None) is None

    def test_invalid_string(self):
        assert _safe_int("not_a_number") is None

    def test_float_string(self):
        assert _safe_int("3.14") is None

    def test_empty_string(self):
        assert _safe_int("") is None

    def test_object(self):
        assert _safe_int({}) is None
