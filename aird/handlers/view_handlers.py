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

from aird.handlers.base_handler import BaseHandler, get_username_string_for_db
from aird.adapters.persistence_adapter import get_all_shares, get_user_favorites
from aird.utils.util import (
    get_files_in_directory,
    get_file_icon,
    is_feature_enabled,
    get_current_feature_flags,
    sanitize_cloud_filename,
    augment_with_shared_status,
)
from aird.core.security import (  # noqa: F401
    is_within_root,
    is_valid_websocket_origin,
    join_path,
)
from aird.core.mmap_handler import MMapFileHandler
from aird.config import (
    ROOT_DIR,
    MAX_READABLE_FILE_SIZE,
    CLOUD_MANAGER,
)
import aird.constants as constants_module
from aird.handlers.constants import (
    APPLICATION_OCTET_STREAM,
)
from aird.constants import CHUNK_SIZE
from aird.cloud import CloudManager, CloudProviderError

# ---------------------------------------------------------------------------
# Helpers for MainHandler.serve_file (reduce cognitive complexity)
# ---------------------------------------------------------------------------

DOWNLOAD_DISABLED_MSG = (
    "Feature disabled: File download is currently disabled by administrator"
)


async def _serve_download(handler, abspath, filename):
    """Serve file as attachment, with optional gzip compression."""
    if not handler.require_feature("file_download", True, body=DOWNLOAD_DISABLED_MSG):
        return
    handler.set_header("Content-Disposition", f'attachment; filename="{filename}"')
    mime_type, _ = mimetypes.guess_type(abspath)
    mime_type = mime_type or APPLICATION_OCTET_STREAM
    handler.set_header("Content-Type", mime_type)

    if is_feature_enabled("compression", True):
        compressible = (
            "text/",
            "application/json",
            "application/javascript",
            "application/xml",
        )
        if any(mime_type.startswith(p) for p in compressible):
            handler.set_header("Content-Encoding", "gzip")

            def compress_file():
                buffer = BytesIO()
                with open(abspath, "rb") as f_in, gzip.GzipFile(
                    fileobj=buffer, mode="wb"
                ) as f_out:
                    shutil.copyfileobj(f_in, f_out)
                return buffer.getvalue()

            with concurrent.futures.ThreadPoolExecutor() as executor:
                compressed_data = await asyncio.get_event_loop().run_in_executor(
                    executor, compress_file
                )
            handler.write(compressed_data)
            await handler.flush()
            return

    async for chunk in MMapFileHandler.serve_file_chunk(abspath):
        handler.write(chunk)
        await handler.flush()


async def _serve_raw_mode(handler, abspath):
    """Serve raw file content (inline) for client-side consumption."""
    try:
        mime_type = APPLICATION_OCTET_STREAM
        try:
            guessed_type, _ = mimetypes.guess_type(abspath)
            if guessed_type:
                mime_type = guessed_type
        except Exception:
            pass
        handler.set_header("Content-Type", mime_type)
        handler.set_header("Content-Disposition", "inline")
        file_size = os.path.getsize(abspath)
        if MMapFileHandler.should_use_mmap(file_size):
            async for chunk in MMapFileHandler.serve_file_chunk(abspath):
                handler.write(chunk)
                await handler.flush()
        else:
            async with aiofiles.open(abspath, "rb") as f:
                while True:
                    chunk = await f.read(CHUNK_SIZE)
                    if not chunk:
                        break
                    handler.write(chunk)
                    await handler.flush()
    except Exception as e:
        logging.error("Error serving raw file: %s", e)
        handler.set_status(500)
        handler.write("Error serving file")


def _serve_pdf_preview(handler, abspath, filename):
    """Render PDF preview page."""
    rel_path = os.path.relpath(abspath, ROOT_DIR).replace("\\", "/")
    handler.render(
        "pdf_preview.html",
        filename=filename,
        path=rel_path,
        pdf_url=handler.request.path + "?mode=raw",
        pdf_download_url=handler.request.path + "?download=1",
    )


def _serve_file_view(handler, abspath, filename):
    """Render file view template (client-side fetch)."""
    file_size = 0
    try:
        file_size = os.path.getsize(abspath)
    except OSError:
        pass
    rel_path = os.path.relpath(abspath, ROOT_DIR).replace("\\", "/")
    handler.render(
        "file.html",
        filename=filename,
        path=rel_path,
        lines=[],
        start_line=1,
        end_line=0,
        reached_EOF=False,
        open_editor=handler.get_argument("open_editor", "False"),
        full_file_content="",
        file_size=file_size,
    )


class RootHandler(BaseHandler):
    def get(self):
        self.redirect("/files/")


class MainHandler(BaseHandler):
    @tornado.web.authenticated
    async def get(self, path):
        abspath = os.path.abspath(os.path.join(ROOT_DIR, path))

        if not is_within_root(abspath, ROOT_DIR):
            self.set_status(403)
            self.write(
                "Access denied: You don't have permission to perform this action"
            )
            return

        if os.path.isdir(abspath):
            files = get_files_in_directory(abspath)

            # Augment file data with shared status
            db_conn = self.db_conn
            if db_conn:
                augment_with_shared_status(files, path, get_all_shares(db_conn))

            parent_path = os.path.dirname(path) if path else None
            # Use SQLite-backed flags for template
            flags_for_template = get_current_feature_flags()
            # Fetch user favorites
            user_favorites = set()
            if db_conn and flags_for_template.get("favorites"):
                username = get_username_string_for_db(self)
                if username:
                    user_favorites = set(get_user_favorites(db_conn, username))
            self.render(
                "browse.html",
                current_path=path,
                parent_path=parent_path,
                files=files,
                join_path=join_path,
                get_file_icon=get_file_icon,
                features=flags_for_template,
                max_file_size=constants_module.MAX_FILE_SIZE,
                user_favorites=user_favorites,
            )
        elif os.path.isfile(abspath):
            await self.serve_file(self, abspath)
        else:
            self.set_status(404)
            self.write(
                "File not found: The requested file may have been moved or deleted"
            )

    @staticmethod
    async def serve_file(handler, abspath):
        filename = os.path.basename(abspath)
        if handler.get_argument("download", None):
            await _serve_download(handler, abspath, filename)
            return
        mode = handler.get_argument("mode", "view")
        if mode == "raw":
            await _serve_raw_mode(handler, abspath)
            return
        if filename.lower().endswith(".pdf"):
            _serve_pdf_preview(handler, abspath, filename)
            return
        _serve_file_view(handler, abspath, filename)


class EditViewHandler(BaseHandler):
    @tornado.web.authenticated
    async def get(self, path):
        if not self.require_feature(
            "file_edit",
            True,
            body="Feature disabled: File editing is currently disabled by administrator",
        ):
            return

        abspath = os.path.abspath(os.path.join(ROOT_DIR, path))
        if not is_within_root(abspath, ROOT_DIR):
            self.set_status(403)
            self.write(
                "Access denied: You don't have permission to perform this action"
            )
            return
        if not os.path.isfile(abspath):
            self.set_status(404)
            self.write(
                "File not found: The requested file may have been moved or deleted"
            )
            return

        # Prevent loading extremely large files into memory in the editor
        try:
            file_size = os.path.getsize(abspath)
        except OSError:
            file_size = 0
        if file_size > MAX_READABLE_FILE_SIZE:
            self.set_status(413)
            self.write(
                f"File too large to edit in browser. Size: {file_size} bytes (limit {MAX_READABLE_FILE_SIZE} bytes)"
            )
            return

        filename = os.path.basename(abspath)

        # Use async file loading to prevent blocking event loop
        try:
            file_size = os.path.getsize(abspath)
            if MMapFileHandler.should_use_mmap(file_size):
                # For large files, still use mmap but in a thread to avoid blocking
                def read_mmap():
                    with open(abspath, "rb") as f:
                        with mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ) as mm:
                            return mm[:].decode("utf-8", errors="replace")

                with concurrent.futures.ThreadPoolExecutor() as executor:
                    full_file_content = await asyncio.get_event_loop().run_in_executor(
                        executor, read_mmap
                    )
            else:
                # Use aiofiles for small files
                async with aiofiles.open(
                    abspath, "r", encoding="utf-8", errors="replace"
                ) as f:
                    full_file_content = await f.read()
        except (OSError, UnicodeDecodeError):
            # Fallback to async read
            async with aiofiles.open(
                abspath, "r", encoding="utf-8", errors="replace"
            ) as f:
                full_file_content = await f.read()

        total_lines = full_file_content.count("\n") + 1 if full_file_content else 0
        is_markdown = filename.lower().endswith(".md")

        self.render(
            "edit.html",
            filename=filename,
            path=path,
            full_file_content=full_file_content,
            total_lines=total_lines,
            features=get_current_feature_flags(),
            is_markdown=is_markdown,
        )


class CloudProviderMixin:
    def get_cloud_provider(self, provider_name: str):
        manager: CloudManager = self.application.settings.get(
            "cloud_manager", CLOUD_MANAGER
        )
        provider = manager.get(provider_name)
        if not provider:
            self.set_status(404)
            self.write({"error": "Provider not configured"})
            return None
        return provider

    def get_cloud_manager(self):
        return self.application.settings.get("cloud_manager", CLOUD_MANAGER)


class CloudProvidersHandler(BaseHandler, CloudProviderMixin):
    @tornado.web.authenticated
    def get(self):
        manager: CloudManager = self.get_cloud_manager()
        providers = [
            {
                "name": provider.name,
                "label": provider.label,
                "root": provider.root_identifier,
            }
            for provider in manager.list_providers()
        ]
        self.write({"providers": providers})


class CloudFilesHandler(BaseHandler, CloudProviderMixin):
    @tornado.web.authenticated
    async def get(self, provider_name: str):
        provider = self.get_cloud_provider(provider_name)
        if not provider:
            return

        folder_id = self.get_query_argument("folder", provider.root_identifier)
        try:
            files = await asyncio.to_thread(
                provider.list_files, folder_id or provider.root_identifier
            )
        except CloudProviderError as exc:
            self.set_status(400)
            self.write({"error": str(exc)})
            return
        except Exception:
            logging.exception(
                "Failed to list cloud files for provider %s", provider_name
            )
            self.set_status(500)
            self.write({"error": "Failed to load cloud files"})
            return

        payload = {
            "provider": provider_name,
            "folder": folder_id or provider.root_identifier,
            "files": [cloud_file.to_dict() for cloud_file in files],
        }
        self.write(payload)


class CloudDownloadHandler(BaseHandler, CloudProviderMixin):
    @tornado.web.authenticated
    async def get(self, provider_name: str):
        provider = self.get_cloud_provider(provider_name)
        if not provider:
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

        filename = sanitize_cloud_filename(
            requested_name or getattr(download, "name", None)
        )
        if not filename:
            filename = f"{provider_name}-file"

        self.set_header(
            "Content-Type", download.content_type or APPLICATION_OCTET_STREAM
        )
        disposition_name = filename.replace('"', "_")
        self.set_header(
            "Content-Disposition", f'attachment; filename="{disposition_name}"'
        )
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
