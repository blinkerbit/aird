import tornado.web
import os
import mimetypes
import gzip
from io import BytesIO
import shutil
import asyncio
import aiofiles
import mmap
import concurrent.futures
import logging
from datetime import datetime

from aird.handlers.base_handler import BaseHandler
from aird.db import get_all_shares
from aird.utils.util import (
    is_within_root,
    get_files_in_directory,
    get_file_icon,
    join_path,
    is_feature_enabled,
    get_current_feature_flags,
    sanitize_cloud_filename,
    MMapFileHandler,
)
from aird.config import (
    ROOT_DIR,
    MAX_FILE_SIZE,
    MAX_READABLE_FILE_SIZE,
    CLOUD_MANAGER,
)
import aird.constants as constants_module
from aird.cloud import CloudManager, CloudProviderError


class RootHandler(BaseHandler):
    def get(self):
        self.redirect("/files/")

class MainHandler(BaseHandler):
    @tornado.web.authenticated
    async def get(self, path):
        abspath = os.path.abspath(os.path.join(ROOT_DIR, path))

        if not is_within_root(abspath, ROOT_DIR):
            self.set_status(403)
            self.write("Access denied: You don't have permission to perform this action")
            return

        if os.path.isdir(abspath):
            # Collect all shared paths from database
            all_shared_paths = set()
            db_conn = constants_module.DB_CONN
            if db_conn:
                all_shares = get_all_shares(db_conn)
                for share in all_shares.values():
                    for p in share.get('paths', []):
                        all_shared_paths.add(p)

            files = get_files_in_directory(abspath)
            
            # Augment file data with shared status
            for file_info in files:
                full_path = join_path(path, file_info['name'])
                file_info['is_shared'] = full_path in all_shared_paths

            parent_path = os.path.dirname(path) if path else None
            # Use SQLite-backed flags for template
            flags_for_template = get_current_feature_flags()
            self.render(
                "browse.html", 
                current_path=path, 
                parent_path=parent_path, 
                files=files,
                join_path=join_path,
                get_file_icon=get_file_icon,
                features=flags_for_template,
                max_file_size=MAX_FILE_SIZE
            )
        elif os.path.isfile(abspath):
            await self.serve_file(self, abspath)
        else:
            self.set_status(404)
            self.write("File not found: The requested file may have been moved or deleted")
    
    @staticmethod
    async def serve_file(handler, abspath):
        filename = os.path.basename(abspath)
        if handler.get_argument('download', None):
            if not is_feature_enabled("file_download", True):
                handler.set_status(403)
                handler.write("Feature disabled: File download is currently disabled by administrator")
                return

            handler.set_header('Content-Disposition', f'attachment; filename="{filename}"')

            # Guess MIME type
            mime_type, _ = mimetypes.guess_type(abspath)
            mime_type = mime_type or "application/octet-stream"
            handler.set_header('Content-Type', mime_type)

            # Check for compressible types
            if is_feature_enabled("compression", True):
                compressible_types = ['text/', 'application/json', 'application/javascript', 'application/xml']
                if any(mime_type.startswith(prefix) for prefix in compressible_types):
                    handler.set_header("Content-Encoding", "gzip")

                    # Fallback to Python gzip compression with async I/O
                    def compress_file():
                        buffer = BytesIO()
                        with open(abspath, 'rb') as f_in, gzip.GzipFile(fileobj=buffer, mode='wb') as f_out:
                            shutil.copyfileobj(f_in, f_out)
                        return buffer.getvalue()
                    
                    import concurrent.futures
                    with concurrent.futures.ThreadPoolExecutor() as executor:
                        compressed_data = await asyncio.get_event_loop().run_in_executor(
                            executor, compress_file
                        )

                    handler.write(compressed_data)
                    await handler.flush()
                    return

            # Fallback to Python mmap implementation
            async for chunk in MMapFileHandler.serve_file_chunk(abspath):
                handler.write(chunk)
                await handler.flush()
            return

        # File viewing logic from the original MainHandler
        # ...

class EditViewHandler(BaseHandler):
    @tornado.web.authenticated
    async def get(self, path):
        if not is_feature_enabled("file_edit", True):
            self.set_status(403)
            self.write("Feature disabled: File editing is currently disabled by administrator")
            return

        abspath = os.path.abspath(os.path.join(ROOT_DIR, path))
        if not is_within_root(abspath, ROOT_DIR):
            self.set_status(403)
            self.write("Access denied: You don't have permission to perform this action")
            return
        if not os.path.isfile(abspath):
            self.set_status(404)
            self.write("File not found: The requested file may have been moved or deleted")
            return

        # Prevent loading extremely large files into memory in the editor
        try:
            file_size = os.path.getsize(abspath)
        except OSError:
            file_size = 0
        if file_size > MAX_READABLE_FILE_SIZE:
            self.set_status(413)
            self.write(f"File too large to edit in browser. Size: {file_size} bytes (limit {MAX_READABLE_FILE_SIZE} bytes)")
            return

        filename = os.path.basename(abspath)
        
        # Use async file loading to prevent blocking event loop
        try:
            file_size = os.path.getsize(abspath)
            if MMapFileHandler.should_use_mmap(file_size):
                # For large files, still use mmap but in a thread to avoid blocking
                def read_mmap():
                    with open(abspath, 'rb') as f:
                        with mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ) as mm:
                            return mm[:].decode('utf-8', errors='replace')
                
                with concurrent.futures.ThreadPoolExecutor() as executor:
                    full_file_content = await asyncio.get_event_loop().run_in_executor(
                        executor, read_mmap
                    )
            else:
                # Use aiofiles for small files
                async with aiofiles.open(abspath, 'r', encoding='utf-8', errors='replace') as f:
                    full_file_content = await f.read()
        except (OSError, UnicodeDecodeError):
            # Fallback to async read
            async with aiofiles.open(abspath, 'r', encoding='utf-8', errors='replace') as f:
                full_file_content = await f.read()
                
        total_lines = full_file_content.count('\n') + 1 if full_file_content else 0

        self.render(
            "edit.html",
            filename=filename,
            path=path,
            full_file_content=full_file_content,
            total_lines=total_lines,
            features=get_current_feature_flags(),
        )

class CloudProvidersHandler(BaseHandler):
    @tornado.web.authenticated
    def get(self):
        manager: CloudManager = self.application.settings.get("cloud_manager", CLOUD_MANAGER)
        providers = [
            {
                "name": provider.name,
                "label": provider.label,
                "root": provider.root_identifier,
            }
            for provider in manager.list_providers()
        ]
        self.write({"providers": providers})


class CloudFilesHandler(BaseHandler):
    @tornado.web.authenticated
    async def get(self, provider_name: str):
        manager: CloudManager = self.application.settings.get("cloud_manager", CLOUD_MANAGER)
        provider = manager.get(provider_name)
        if not provider:
            self.set_status(404)
            self.write({"error": "Provider not configured"})
            return

        folder_id = self.get_query_argument("folder", provider.root_identifier)
        try:
            files = await asyncio.to_thread(provider.list_files, folder_id or provider.root_identifier)
        except CloudProviderError as exc:
            self.set_status(400)
            self.write({"error": str(exc)})
            return
        except Exception:
            logging.exception("Failed to list cloud files for provider %s", provider_name)
            self.set_status(500)
            self.write({"error": "Failed to load cloud files"})
            return

        payload = {
            "provider": provider_name,
            "folder": folder_id or provider.root_identifier,
            "files": [cloud_file.to_dict() for cloud_file in files],
        }
        self.write(payload)


class CloudDownloadHandler(BaseHandler):
    @tornado.web.authenticated
    async def get(self, provider_name: str):
        manager: CloudManager = self.application.settings.get("cloud_manager", CLOUD_MANAGER)
        provider = manager.get(provider_name)
        if not provider:
            self.set_status(404)
            self.write({"error": "Provider not configured"})
            return

        file_id = self.get_query_argument("file_id", "").strip()
        if not file_id:
            self.set_status(400)
            self.write({"error": "file_id is required"})
            return

        requested_name = self.get_query_argument("file_name", "").strip()

        try:
            download = await asyncio.to_thread(provider.download_file, file_id)
        except CloudProviderError as exc:
            self.set_status(400)
            self.write({"error": str(exc)})
            return
        except Exception:
            logging.exception("Failed to download cloud file from %s", provider_name)
            self.set_status(500)
            self.write({"error": "Failed to download cloud file"})
            return

        filename = sanitize_cloud_filename(requested_name or getattr(download, "name", None))
        if not filename:
            filename = f"{provider_name}-file"

        self.set_header("Content-Type", download.content_type or "application/octet-stream")
        disposition_name = filename.replace('"', '_')
        self.set_header("Content-Disposition", f'attachment; filename="{disposition_name}"')
        if download.content_length:
            self.set_header("Content-Length", str(download.content_length))

        iterator = download.iter_chunks()
        try:
            while True:
                chunk = await asyncio.to_thread(next, iterator, None)
                if not chunk:
                    break
                self.write(chunk)
                await self.flush()
        finally:
            download.close()

class FourOhFourHandler(BaseHandler):
    def prepare(self):
        self.set_status(404)
        self.render("error.html", error_code=404, error_message="Page not found")

class NoCacheStaticFileHandler(tornado.web.StaticFileHandler):
    def set_extra_headers(self, path):
        self.set_header("Cache-Control", "no-cache, no-store, must-revalidate")
        self.set_header("Pragma", "no-cache")
        self.set_header("Expires", "0")

