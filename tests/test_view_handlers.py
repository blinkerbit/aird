
import pytest
from unittest.mock import patch, MagicMock, AsyncMock
from aird.handlers.view_handlers import (
    RootHandler, MainHandler, EditViewHandler, CloudProvidersHandler, CloudFilesHandler, CloudDownloadHandler,
    FourOhFourHandler, NoCacheStaticFileHandler
)
from aird.cloud import CloudProviderError
import json

class TestRootHandler:
    def setup_method(self):
        self.mock_app = MagicMock()
        self.mock_request = MagicMock()
        self.mock_app.settings = {'cookie_secret': 'test_secret'}

    def test_root_redirect(self):
        handler = RootHandler(self.mock_app, self.mock_request)
        with patch.object(handler, 'redirect') as mock_redirect:
            handler.get()
            mock_redirect.assert_called_with("/files/")

class TestMainHandler:
    def setup_method(self):
        self.mock_app = MagicMock()
        self.mock_request = MagicMock()
        self.mock_app.settings = {'cookie_secret': 'test_secret'}

    @pytest.mark.asyncio
    async def test_browse_directory(self):
        handler = MainHandler(self.mock_app, self.mock_request)
        handler._current_user = {'username': 'user'}
        
        with patch('os.path.abspath', return_value='/root/dir'), \
             patch('aird.handlers.view_handlers.is_within_root', return_value=True), \
             patch('os.path.isdir', return_value=True), \
             patch('aird.handlers.view_handlers.constants_module.DB_CONN', MagicMock()), \
             patch('aird.handlers.view_handlers.get_all_shares', return_value={}), \
             patch('aird.handlers.view_handlers.get_files_in_directory', return_value=[]), \
             patch('aird.handlers.view_handlers.get_current_feature_flags', return_value={}), \
             patch.object(handler, 'render') as mock_render:
            
            await handler.get("dir")
            mock_render.assert_called()
            assert mock_render.call_args[0][0] == "browse.html"

    @pytest.mark.asyncio
    async def test_browse_access_denied(self):
        handler = MainHandler(self.mock_app, self.mock_request)
        handler._current_user = {'username': 'user'}
        
        with patch('os.path.abspath', return_value='/outside/dir'), \
             patch('aird.handlers.view_handlers.is_within_root', return_value=False), \
             patch.object(handler, 'set_status') as mock_status, \
             patch.object(handler, 'write') as mock_write:
            
            await handler.get("dir")
            mock_status.assert_called_with(403)
            mock_write.assert_called_with("Access denied: You don't have permission to perform this action")

    @pytest.mark.asyncio
    async def test_browse_file_not_found(self):
        handler = MainHandler(self.mock_app, self.mock_request)
        handler._current_user = {'username': 'user'}
        
        with patch('os.path.abspath', return_value='/root/missing'), \
             patch('aird.handlers.view_handlers.is_within_root', return_value=True), \
             patch('os.path.isdir', return_value=False), \
             patch('os.path.isfile', return_value=False), \
             patch.object(handler, 'set_status') as mock_status, \
             patch.object(handler, 'write') as mock_write:
            
            await handler.get("missing")
            mock_status.assert_called_with(404)
            mock_write.assert_called_with("File not found: The requested file may have been moved or deleted")

    @pytest.mark.asyncio
    async def test_serve_file_download_compression(self):
        handler = MainHandler(self.mock_app, self.mock_request)
        handler._current_user = {'username': 'user'}
        
        handler.get_argument = MagicMock(return_value='true')
        
        with patch('os.path.abspath', return_value='/root/file.txt'), \
             patch('aird.handlers.view_handlers.is_within_root', return_value=True), \
             patch('os.path.isdir', return_value=False), \
             patch('os.path.isfile', return_value=True), \
             patch('aird.handlers.view_handlers.is_feature_enabled', return_value=True), \
             patch('mimetypes.guess_type', return_value=('text/plain', None)), \
             patch('builtins.open', new_callable=MagicMock) as mock_open, \
             patch('shutil.copyfileobj') as mock_copy:
            
            mock_file = MagicMock()
            mock_open.return_value.__enter__.return_value = mock_file
            
            def side_effect(f_in, f_out):
                 f_out.write(b'compressed')
            mock_copy.side_effect = side_effect

            with patch.object(handler, 'set_header') as mock_header, \
                 patch.object(handler, 'write') as mock_write, \
                 patch.object(handler, 'flush', new_callable=AsyncMock):
                
                await handler.get("file.txt")
                
                mock_header.assert_any_call('Content-Encoding', 'gzip')
                # Check that write was called with gzip data (magic bytes)
                args = mock_write.call_args[0]
                assert len(args) == 1
                assert args[0].startswith(b'\x1f\x8b')

    @pytest.mark.asyncio
    async def test_serve_file_download_no_compression(self):
        handler = MainHandler(self.mock_app, self.mock_request)
        handler._current_user = {'username': 'user'}
        
        handler.get_argument = MagicMock(return_value='true')
        
        with patch('os.path.abspath', return_value='/root/image.png'), \
             patch('aird.handlers.view_handlers.is_within_root', return_value=True), \
             patch('os.path.isdir', return_value=False), \
             patch('os.path.isfile', return_value=True), \
             patch('aird.handlers.view_handlers.is_feature_enabled', return_value=True), \
             patch('mimetypes.guess_type', return_value=('image/png', None)), \
             patch('aird.handlers.view_handlers.MMapFileHandler.serve_file_chunk', return_value=AsyncMock()) as mock_serve_chunk:
            
            async def async_gen(path):
                yield b'image_data'
            mock_serve_chunk.side_effect = async_gen

            with patch.object(handler, 'set_header') as mock_header, \
                 patch.object(handler, 'write') as mock_write, \
                 patch.object(handler, 'flush', new_callable=AsyncMock):
                
                await handler.get("image.png")
                
                mock_write.assert_called_with(b'image_data')

    @pytest.mark.asyncio
    async def test_serve_file_download_disabled(self):
        handler = MainHandler(self.mock_app, self.mock_request)
        handler._current_user = {'username': 'user'}
        
        handler.get_argument = MagicMock(return_value='true')
        
        with patch('os.path.abspath', return_value='/root/file.txt'), \
             patch('aird.handlers.view_handlers.is_within_root', return_value=True), \
             patch('os.path.isdir', return_value=False), \
             patch('os.path.isfile', return_value=True), \
             patch('aird.handlers.view_handlers.is_feature_enabled', return_value=False), \
             patch.object(handler, 'set_status') as mock_status, \
             patch.object(handler, 'write') as mock_write:
            
            await handler.get("file.txt")
            mock_status.assert_called_with(403)
            mock_write.assert_called_with("Feature disabled: File download is currently disabled by administrator")

    @pytest.mark.asyncio
    async def test_browse_shared_files(self):
        handler = MainHandler(self.mock_app, self.mock_request)
        handler._current_user = {'username': 'user'}
        
        with patch('os.path.abspath', return_value='/root/dir'), \
             patch('aird.handlers.view_handlers.is_within_root', return_value=True), \
             patch('os.path.isdir', return_value=True), \
             patch('aird.handlers.view_handlers.constants_module.DB_CONN', MagicMock()), \
             patch('aird.handlers.view_handlers.get_all_shares', return_value={'share1': {'paths': ['dir/file1']}}), \
             patch('aird.handlers.view_handlers.get_files_in_directory', return_value=[{'name': 'file1'}, {'name': 'file2'}]), \
             patch('aird.handlers.view_handlers.join_path', side_effect=lambda p, n: f"{p}/{n}" if p else n), \
             patch('aird.handlers.view_handlers.get_current_feature_flags', return_value={}), \
             patch.object(handler, 'render') as mock_render:
            
            await handler.get("dir")
            mock_render.assert_called()
            files = mock_render.call_args[1]['files']
            # Check if is_shared is set correctly
            # file1 should be shared
            assert files[0]['is_shared'] == True
            assert files[1]['is_shared'] == False

    @pytest.mark.asyncio
    async def test_serve_file_view(self):
        handler = MainHandler(self.mock_app, self.mock_request)
        handler._current_user = {'username': 'user'}
        
        # Mock get_argument to return None for 'download'
        def get_arg(name, default=None):
            if name == 'download': return None
            if name == 'mode': return None
            return default
        handler.get_argument = get_arg
        
        with patch('os.path.abspath', return_value='/root/file.txt'), \
             patch('aird.handlers.view_handlers.is_within_root', return_value=True), \
             patch('os.path.isdir', return_value=False), \
             patch('os.path.isfile', return_value=True), \
             patch('os.path.getsize', return_value=100), \
             patch('aird.handlers.view_handlers.ROOT_DIR', '/root'), \
             patch.object(handler, 'render') as mock_render:
            
            # File fetching is now client-side, so serve_file just renders the template
            
            await handler.get("file.txt")
            mock_render.assert_called()
            # Verify render arguments
            args = mock_render.call_args
            assert args[0][0] == "file.html"
            kwargs = args[1]
            assert kwargs['lines'] == [] # Should be empty for client-side rendering
            assert kwargs['file_size'] == 100

    @pytest.mark.asyncio
    async def test_serve_file_raw(self):
        handler = MainHandler(self.mock_app, self.mock_request)
        handler._current_user = {'username': 'user'}
        
        def get_arg(name, default=None):
            if name == 'mode': return 'raw'
            return default
        handler.get_argument = get_arg
        
        with patch('os.path.abspath', return_value='/root/file.txt'), \
             patch('aird.handlers.view_handlers.is_within_root', return_value=True), \
             patch('os.path.isdir', return_value=False), \
             patch('os.path.isfile', return_value=True), \
             patch('os.path.getsize', return_value=100), \
             patch('aird.handlers.view_handlers.MMapFileHandler.should_use_mmap', return_value=False), \
             patch('mimetypes.guess_type', return_value=('text/plain', None)), \
             patch('aiofiles.open', new_callable=MagicMock) as mock_open, \
             patch('aird.handlers.view_handlers.ROOT_DIR', '/root'), \
             patch.object(handler, 'write') as mock_write, \
             patch.object(handler, 'flush', new_callable=AsyncMock), \
             patch.object(handler, 'set_header'):
            
            mock_file = AsyncMock()
            mock_file.read.side_effect = [b"content", b""]
            
            # Mock async context manager for aiofiles.open
            mock_cm = MagicMock()
            mock_cm.__aenter__ = AsyncMock(return_value=mock_file)
            mock_cm.__aexit__ = AsyncMock(return_value=None)
            mock_open.return_value = mock_cm
            
            await handler.get("file.txt")
            
            mock_write.assert_called_with(b"content")


class TestEditViewHandler:
    def setup_method(self):
        self.mock_app = MagicMock()
        self.mock_request = MagicMock()
        self.mock_app.settings = {'cookie_secret': 'test_secret'}

    @pytest.mark.asyncio
    async def test_edit_view(self):
        handler = EditViewHandler(self.mock_app, self.mock_request)
        handler._current_user = {'username': 'user'}
        
        with patch('aird.handlers.view_handlers.is_feature_enabled', return_value=True), \
             patch('os.path.abspath', return_value='/root/test.txt'), \
             patch('aird.handlers.view_handlers.is_within_root', return_value=True), \
             patch('os.path.isfile', return_value=True), \
             patch('os.path.getsize', return_value=100), \
             patch('aiofiles.open', new_callable=MagicMock) as mock_open, \
             patch('aird.handlers.view_handlers.get_current_feature_flags', return_value={}), \
             patch.object(handler, 'render') as mock_render:
            
            mock_file = AsyncMock()
            mock_file.read.return_value = "content"
            mock_open.return_value.__aenter__.return_value = mock_file
            
            await handler.get("test.txt")
            mock_render.assert_called()
            assert mock_render.call_args[0][0] == "edit.html"

    @pytest.mark.asyncio
    async def test_edit_feature_disabled(self):
        handler = EditViewHandler(self.mock_app, self.mock_request)
        handler._current_user = {'username': 'user'}
        
        with patch('aird.handlers.view_handlers.is_feature_enabled', return_value=False), \
             patch.object(handler, 'set_status') as mock_status, \
             patch.object(handler, 'write') as mock_write:
            
            await handler.get("test.txt")
            mock_status.assert_called_with(403)
            mock_write.assert_called_with("Feature disabled: File editing is currently disabled by administrator")

    @pytest.mark.asyncio
    async def test_edit_file_too_large(self):
        handler = EditViewHandler(self.mock_app, self.mock_request)
        handler._current_user = {'username': 'user'}
        
        with patch('aird.handlers.view_handlers.is_feature_enabled', return_value=True), \
             patch('os.path.abspath', return_value='/root/large.txt'), \
             patch('aird.handlers.view_handlers.is_within_root', return_value=True), \
             patch('os.path.isfile', return_value=True), \
             patch('os.path.getsize', return_value=10 * 1024 * 1024 * 1024), \
             patch('aird.handlers.view_handlers.MAX_READABLE_FILE_SIZE', 1024), \
             patch.object(handler, 'set_status') as mock_status, \
             patch.object(handler, 'write') as mock_write:
            
            await handler.get("large.txt")
            mock_status.assert_called_with(413)
            assert "File too large" in mock_write.call_args[0][0]

    @pytest.mark.asyncio
    async def test_edit_access_denied(self):
        handler = EditViewHandler(self.mock_app, self.mock_request)
        handler._current_user = {'username': 'user'}
        
        with patch('aird.handlers.view_handlers.is_feature_enabled', return_value=True), \
             patch('os.path.abspath', return_value='/outside/file.txt'), \
             patch('aird.handlers.view_handlers.is_within_root', return_value=False), \
             patch.object(handler, 'set_status') as mock_status, \
             patch.object(handler, 'write') as mock_write:
            
            await handler.get("file.txt")
            mock_status.assert_called_with(403)
            mock_write.assert_called_with("Access denied: You don't have permission to perform this action")

    @pytest.mark.asyncio
    async def test_edit_file_not_found(self):
        handler = EditViewHandler(self.mock_app, self.mock_request)
        handler._current_user = {'username': 'user'}
        
        with patch('aird.handlers.view_handlers.is_feature_enabled', return_value=True), \
             patch('os.path.abspath', return_value='/root/missing.txt'), \
             patch('aird.handlers.view_handlers.is_within_root', return_value=True), \
             patch('os.path.isfile', return_value=False), \
             patch.object(handler, 'set_status') as mock_status, \
             patch.object(handler, 'write') as mock_write:
            
            await handler.get("missing.txt")
            mock_status.assert_called_with(404)
            mock_write.assert_called_with("File not found: The requested file may have been moved or deleted")

    @pytest.mark.asyncio
    async def test_edit_file_size_oserror(self):
        handler = EditViewHandler(self.mock_app, self.mock_request)
        handler._current_user = {'username': 'user'}
        
        with patch('aird.handlers.view_handlers.is_feature_enabled', return_value=True), \
             patch('os.path.abspath', return_value='/root/file.txt'), \
             patch('aird.handlers.view_handlers.is_within_root', return_value=True), \
             patch('os.path.isfile', return_value=True), \
             patch('os.path.getsize', side_effect=[OSError, 100]), \
             patch('aiofiles.open', new_callable=MagicMock) as mock_open, \
             patch.object(handler, 'render') as mock_render:
            
            mock_file = AsyncMock()
            mock_file.read.return_value = "content"
            mock_open.return_value.__aenter__.return_value = mock_file
            
            await handler.get("file.txt")
            mock_render.assert_called()

    @pytest.mark.asyncio
    async def test_edit_mmap_read(self):
        handler = EditViewHandler(self.mock_app, self.mock_request)
        handler._current_user = {'username': 'user'}
        
        with patch('aird.handlers.view_handlers.is_feature_enabled', return_value=True), \
             patch('os.path.abspath', return_value='/root/large.txt'), \
             patch('aird.handlers.view_handlers.is_within_root', return_value=True), \
             patch('os.path.isfile', return_value=True), \
             patch('os.path.getsize', return_value=1000000), \
             patch('aird.handlers.view_handlers.MMapFileHandler.should_use_mmap', return_value=True), \
             patch('builtins.open', new_callable=MagicMock) as mock_open, \
             patch('mmap.mmap', new_callable=MagicMock) as mock_mmap, \
             patch.object(handler, 'render') as mock_render:
            
            mock_mm = MagicMock()
            mock_mm.__getitem__.return_value = b"content"
            mock_mm.__enter__.return_value = mock_mm
            mock_mmap.return_value = mock_mm
            
            await handler.get("large.txt")
            mock_render.assert_called()
            assert mock_render.call_args[1]['full_file_content'] == "content"

    @pytest.mark.asyncio
    async def test_edit_fallback_read(self):
        handler = EditViewHandler(self.mock_app, self.mock_request)
        handler._current_user = {'username': 'user'}
        
        with patch('aird.handlers.view_handlers.is_feature_enabled', return_value=True), \
             patch('os.path.abspath', return_value='/root/file.txt'), \
             patch('aird.handlers.view_handlers.is_within_root', return_value=True), \
             patch('os.path.isfile', return_value=True), \
             patch('os.path.getsize', return_value=100), \
             patch('aird.handlers.view_handlers.MMapFileHandler.should_use_mmap', return_value=False), \
             patch('aiofiles.open', new_callable=MagicMock) as mock_open, \
             patch.object(handler, 'render') as mock_render:
            
            cm1 = MagicMock()
            cm1.__aenter__.side_effect = OSError("Read failed")
            
            mock_file_success = AsyncMock()
            mock_file_success.read.return_value = "content"
            cm2 = MagicMock()
            cm2.__aenter__.return_value = mock_file_success
            
            mock_open.side_effect = [cm1, cm2]
            
            await handler.get("file.txt")
            mock_render.assert_called()
            assert mock_render.call_args[1]['full_file_content'] == "content"

class TestCloudProvidersHandler:
    def setup_method(self):
        self.mock_app = MagicMock()
        self.mock_request = MagicMock()
        self.mock_app.settings = {'cookie_secret': 'test_secret'}

    def test_list_providers(self):
        handler = CloudProvidersHandler(self.mock_app, self.mock_request)
        handler._current_user = {'username': 'user'}
        
        mock_provider = MagicMock()
        mock_provider.name = 'provider1'
        mock_provider.label = 'Provider 1'
        mock_provider.root_identifier = 'root'
        
        mock_manager = MagicMock()
        mock_manager.list_providers.return_value = [mock_provider]
        self.mock_app.settings['cloud_manager'] = mock_manager
        
        with patch.object(handler, 'write') as mock_write:
            handler.get()
            mock_write.assert_called()
            assert mock_write.call_args[0][0]['providers'][0]['name'] == 'provider1'

class TestCloudFilesHandler:
    def setup_method(self):
        self.mock_app = MagicMock()
        self.mock_request = MagicMock()
        self.mock_app.settings = {'cookie_secret': 'test_secret'}

    @pytest.mark.asyncio
    async def test_list_files_success(self):
        handler = CloudFilesHandler(self.mock_app, self.mock_request)
        handler._current_user = {'username': 'user'}
        
        mock_provider = MagicMock()
        mock_provider.root_identifier = 'root'
        mock_file = MagicMock()
        mock_file.to_dict.return_value = {'name': 'file1'}
        mock_provider.list_files.return_value = [mock_file]
        
        mock_manager = MagicMock()
        mock_manager.get.return_value = mock_provider
        self.mock_app.settings['cloud_manager'] = mock_manager
        
        # Mock get_query_argument
        handler.get_query_argument = MagicMock(return_value=None)

        with patch.object(handler, 'write') as mock_write:
            await handler.get('provider1')
            mock_write.assert_called()
            args = mock_write.call_args[0][0]
            assert args['provider'] == 'provider1'
            assert args['files'][0]['name'] == 'file1'

    @pytest.mark.asyncio
    async def test_list_files_provider_not_found(self):
        handler = CloudFilesHandler(self.mock_app, self.mock_request)
        handler._current_user = {'username': 'user'}
        
        mock_manager = MagicMock()
        mock_manager.get.return_value = None
        self.mock_app.settings['cloud_manager'] = mock_manager
        
        with patch.object(handler, 'set_status') as mock_status, \
             patch.object(handler, 'write') as mock_write:
            await handler.get('provider1')
            mock_status.assert_called_with(404)
            mock_write.assert_called_with({"error": "Provider not configured"})

    @pytest.mark.asyncio
    async def test_list_files_error(self):
        handler = CloudFilesHandler(self.mock_app, self.mock_request)
        handler._current_user = {'username': 'user'}
        
        mock_provider = MagicMock()
        mock_provider.root_identifier = 'root'
        mock_provider.list_files.side_effect = Exception("Boom")
        
        mock_manager = MagicMock()
        mock_manager.get.return_value = mock_provider
        self.mock_app.settings['cloud_manager'] = mock_manager
        
        handler.get_query_argument = MagicMock(return_value=None)

        with patch.object(handler, 'set_status') as mock_status, \
             patch.object(handler, 'write') as mock_write, \
             patch('logging.exception') as mock_log:
            await handler.get('provider1')
            mock_status.assert_called_with(500)
            mock_write.assert_called_with({"error": "Failed to load cloud files"})

    @pytest.mark.asyncio
    async def test_list_files_cloud_provider_error(self):
        handler = CloudFilesHandler(self.mock_app, self.mock_request)
        handler._current_user = {'username': 'user'}
        
        mock_provider = MagicMock()
        mock_provider.root_identifier = 'root'
        mock_provider.list_files.side_effect = CloudProviderError("Provider error")
        
        mock_manager = MagicMock()
        mock_manager.get.return_value = mock_provider
        self.mock_app.settings['cloud_manager'] = mock_manager
        
        handler.get_query_argument = MagicMock(return_value=None)

        with patch.object(handler, 'set_status') as mock_status, \
             patch.object(handler, 'write') as mock_write:
            await handler.get('provider1')
            mock_status.assert_called_with(400)
            mock_write.assert_called_with({"error": "Provider error"})

class TestCloudDownloadHandler:
    def setup_method(self):
        self.mock_app = MagicMock()
        self.mock_request = MagicMock()
        self.mock_app.settings = {'cookie_secret': 'test_secret'}

    @pytest.mark.asyncio
    async def test_download_success(self):
        handler = CloudDownloadHandler(self.mock_app, self.mock_request)
        handler._current_user = {'username': 'user'}
        
        def get_arg(name, default=None):
            if name == 'file_id': return '123'
            if name == 'file_name': return 'test.txt'
            return default
        handler.get_query_argument = get_arg
        
        mock_download = MagicMock()
        mock_download.name = 'test.txt'
        mock_download.content_type = 'text/plain'
        mock_download.content_length = 100
        mock_download.iter_chunks.return_value = iter([b'chunk1', b'chunk2'])
        
        mock_provider = MagicMock()
        mock_provider.download_file.return_value = mock_download
        
        mock_manager = MagicMock()
        mock_manager.get.return_value = mock_provider
        self.mock_app.settings['cloud_manager'] = mock_manager
        
        with patch.object(handler, 'set_header') as mock_header, \
             patch.object(handler, 'write') as mock_write, \
             patch.object(handler, 'flush', new_callable=AsyncMock) as mock_flush:
            
            await handler.get('provider1')
            
            mock_header.assert_any_call("Content-Type", "text/plain")
            mock_header.assert_any_call("Content-Disposition", 'attachment; filename="test.txt"')
            
            assert mock_write.call_count == 2
            mock_write.assert_any_call(b'chunk1')
            mock_write.assert_any_call(b'chunk2')
            
            mock_download.close.assert_called()

    @pytest.mark.asyncio
    async def test_download_missing_file_id(self):
        handler = CloudDownloadHandler(self.mock_app, self.mock_request)
        handler._current_user = {'username': 'user'}
        
        mock_provider = MagicMock()
        mock_manager = MagicMock()
        mock_manager.get.return_value = mock_provider
        self.mock_app.settings['cloud_manager'] = mock_manager
        
        handler.get_query_argument = MagicMock(return_value="")
        
        with patch.object(handler, 'set_status') as mock_status, \
             patch.object(handler, 'write') as mock_write:
            await handler.get('provider1')
            mock_status.assert_called_with(400)
            mock_write.assert_called_with({"error": "file_id is required"})

    @pytest.mark.asyncio
    async def test_download_provider_not_found(self):
        handler = CloudDownloadHandler(self.mock_app, self.mock_request)
        handler._current_user = {'username': 'user'}
        
        mock_manager = MagicMock()
        mock_manager.get.return_value = None
        self.mock_app.settings['cloud_manager'] = mock_manager
        
        with patch.object(handler, 'set_status') as mock_status, \
             patch.object(handler, 'write') as mock_write:
            await handler.get('provider1')
            mock_status.assert_called_with(404)
            mock_write.assert_called_with({"error": "Provider not configured"})

    @pytest.mark.asyncio
    async def test_download_error(self):
        handler = CloudDownloadHandler(self.mock_app, self.mock_request)
        handler._current_user = {'username': 'user'}
        
        mock_provider = MagicMock()
        mock_provider.download_file.side_effect = Exception("Download failed")
        
        mock_manager = MagicMock()
        mock_manager.get.return_value = mock_provider
        self.mock_app.settings['cloud_manager'] = mock_manager
        
        def get_arg(name, default=None):
            if name == 'file_id': return '123'
            return default
        handler.get_query_argument = get_arg
        
        with patch.object(handler, 'set_status') as mock_status, \
             patch.object(handler, 'write') as mock_write, \
             patch('logging.exception') as mock_log:
            await handler.get('provider1')
            mock_status.assert_called_with(500)
            mock_write.assert_called_with({"error": "Failed to download cloud file"})

    @pytest.mark.asyncio
    async def test_download_cloud_provider_error(self):
        handler = CloudDownloadHandler(self.mock_app, self.mock_request)
        handler._current_user = {'username': 'user'}
        
        mock_provider = MagicMock()
        mock_provider.download_file.side_effect = CloudProviderError("Download error")
        
        mock_manager = MagicMock()
        mock_manager.get.return_value = mock_provider
        self.mock_app.settings['cloud_manager'] = mock_manager
        
        def get_arg(name, default=None):
            if name == 'file_id': return '123'
            return default
        handler.get_query_argument = get_arg
        
        with patch.object(handler, 'set_status') as mock_status, \
             patch.object(handler, 'write') as mock_write:
            await handler.get('provider1')
            mock_status.assert_called_with(400)
            mock_write.assert_called_with({"error": "Download error"})

    @pytest.mark.asyncio
    async def test_download_filename_fallback(self):
        handler = CloudDownloadHandler(self.mock_app, self.mock_request)
        handler._current_user = {'username': 'user'}
        
        mock_download = MagicMock()
        mock_download.name = None # No name
        mock_download.content_type = 'text/plain'
        mock_download.content_length = 100
        mock_download.iter_chunks.return_value = iter([])
        
        mock_provider = MagicMock()
        mock_provider.download_file.return_value = mock_download
        
        mock_manager = MagicMock()
        mock_manager.get.return_value = mock_provider
        self.mock_app.settings['cloud_manager'] = mock_manager
        
        def get_arg(name, default=None):
            if name == 'file_id': return '123'
            if name == 'file_name': return '' # No requested name
            return default
        handler.get_query_argument = get_arg
        
        with patch('aird.handlers.view_handlers.sanitize_cloud_filename', return_value=""), \
             patch.object(handler, 'set_header') as mock_header, \
             patch.object(handler, 'write') as mock_write, \
             patch.object(handler, 'flush', new_callable=AsyncMock):
            
            await handler.get('provider1')
            
            # Should use fallback name
            mock_header.assert_any_call("Content-Disposition", 'attachment; filename="provider1-file"')

class TestFourOhFourHandler:
    def setup_method(self):
        self.mock_app = MagicMock()
        self.mock_request = MagicMock()
        self.mock_app.settings = {'cookie_secret': 'test_secret'}

    def test_prepare(self):
        handler = FourOhFourHandler(self.mock_app, self.mock_request)
        with patch.object(handler, 'set_status') as mock_status, \
             patch.object(handler, 'render') as mock_render:
            handler.prepare()
            mock_status.assert_called_with(404)
            mock_render.assert_called_with("error.html", error_code=404, error_message="Page not found")

class TestNoCacheStaticFileHandler:
    def setup_method(self):
        self.mock_app = MagicMock()
        self.mock_request = MagicMock()
        self.mock_app.settings = {'cookie_secret': 'test_secret'}



    def test_set_extra_headers_logic(self):
        # Create a dummy class that inherits or just use the class
        # We can't easily instantiate StaticFileHandler without real args.
        # But we can patch the __init__ to do nothing.
        
        with patch('tornado.web.StaticFileHandler.__init__', return_value=None):
            handler = NoCacheStaticFileHandler(self.mock_app, self.mock_request)
            handler.set_header = MagicMock()
            
            handler.set_extra_headers("path")
            
            handler.set_header.assert_any_call("Cache-Control", "no-cache, no-store, must-revalidate")
            handler.set_header.assert_any_call("Pragma", "no-cache")
            handler.set_header.assert_any_call("Expires", "0")

