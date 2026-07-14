import tornado.web
import os
import mimetypes
import asyncio
import aiofiles
import concurrent.futures
import mmap
import logging

from aird.handlers.base_handler import (
    BaseHandler,
    get_username_string_for_db,
    get_user_root,
    require_action,
)
from aird.utils.tag_display import tag_chip_inline_style
from aird.utils.util import (
    get_files_in_directory,
    get_file_icon,
    is_feature_enabled,
    get_current_feature_flags,
    sanitize_cloud_filename,
    augment_with_shared_status,
    get_file_size_safe,
    browser_media_kind,
)
from aird.core.security import (  # noqa: F401
    is_within_root,
    is_valid_websocket_origin,
    join_path,
)
from aird.core.mmap_handler import MMapFileHandler
from aird.config import (
    MAX_READABLE_FILE_SIZE,
    CLOUD_MANAGER,
)
import aird.constants as constants_module
from aird.handlers.constants import (
    APPLICATION_OCTET_STREAM,
)
from aird.constants import CHUNK_SIZE
from aird.cloud import CloudManager
from aird.constants.file_ops import PROVIDER_NOT_CONFIGURED
from aird.core.file_operations import get_files_by_tag_patterns, get_tags_for_path
from aird.core.http_range import parse_range_header
from aird.core.compression import negotiate_encoding, should_compress, compress_file
from aird.core.file_send import sendfile_available, sendfile_to_socket
from aird.core.rate_limit import TransferRateLimiter
from aird.db.resource_tags import list_resource_tags
from aird.db.tag_colors import get_tag_colors_map

# ---------------------------------------------------------------------------
# Helpers for MainHandler.serve_file (reduce cognitive complexity)
# ---------------------------------------------------------------------------

DOWNLOAD_DISABLED_MSG = (
    "Feature disabled: File download is currently disabled by administrator"
)


async def _serve_download(handler, abspath, filename):
    """Serve file as attachment with Accept-Ranges and optional Range (206)."""
    if not handler.require_feature("file_download", True, body=DOWNLOAD_DISABLED_MSG):
        return
    user_key = handler.get_display_username() or handler.request.remote_ip or "anonymous"
    if not TransferRateLimiter.try_acquire_concurrent(user_key):
        handler.set_status(429)
        handler.set_header("Retry-After", "5")
        handler.write({"error": "Too many concurrent transfers"})
        return
    try:
        await _serve_download_body(handler, abspath, filename, user_key)
    finally:
        TransferRateLimiter.release_concurrent(user_key)


def _download_socket(handler):
    stream = getattr(handler.request.connection, "stream", None)
    return getattr(stream, "socket", None) if stream else None


def _download_wants_compression(handler, abspath, mime_type, file_size, byte_range):
    encoding = negotiate_encoding(
        handler.request.headers.get("Accept-Encoding"),
        constants_module.COMPRESSION_CONFIG.get("algorithms"),
    )
    if not encoding:
        return None
    if not should_compress(
        path=abspath,
        mime_type=mime_type,
        file_size=file_size,
        has_range=bool(byte_range),
        remote_ip=handler.request.remote_ip or "",
        compression_enabled=is_feature_enabled("compression", True),
        mode=constants_module.COMPRESSION_CONFIG.get("mode", "wan_only"),
        min_bytes=int(constants_module.COMPRESSION_CONFIG.get("min_bytes", 1024)),
        max_bytes=int(constants_module.COMPRESSION_CONFIG.get("max_bytes", 50 * 1024 * 1024)),
        corporate_cidrs=constants_module.CORPORATE_IP_CIDRS,
    ):
        return None
    return encoding


async def _stream_chunks_to_handler(handler, chunk_iter, user_key):
    async for chunk in chunk_iter:
        await TransferRateLimiter.wait_for_bytes(
            user_key, len(chunk), direction="download"
        )
        handler.write(chunk)
        await handler.flush()


async def _try_sendfile_download(handler, abspath, offset, length, user_key):
    if not is_feature_enabled("transfer_sendfile", False) or not sendfile_available():
        return False
    sock = _download_socket(handler)
    if not sock:
        return False
    if not await sendfile_to_socket(sock, abspath, offset, length):
        return False
    await TransferRateLimiter.wait_for_bytes(user_key, length, direction="download")
    return True


async def _serve_download_range(handler, abspath, byte_range, file_size, user_key, use_compression):
    start, end = byte_range.start, byte_range.end
    handler.set_status(206)
    handler.set_header("Content-Range", f"bytes {start}-{end}/{file_size}")
    handler.set_header("Content-Length", str(byte_range.length))
    if handler.request.method == "HEAD":
        return
    if not use_compression and await _try_sendfile_download(
        handler, abspath, start, byte_range.length, user_key
    ):
        return
    await _stream_chunks_to_handler(
        handler,
        MMapFileHandler.serve_file_chunk(abspath, start=start, end=end),
        user_key,
    )


async def _serve_download_compressed(handler, abspath, encoding, user_key):
    handler.set_header("Content-Encoding", encoding)
    handler.set_header("Vary", "Accept-Encoding")
    level = int(constants_module.COMPRESSION_CONFIG.get("level", 6))
    compressed = await compress_file(abspath, encoding, level)
    await TransferRateLimiter.wait_for_bytes(
        user_key, len(compressed), direction="download"
    )
    handler.write(compressed)
    await handler.flush()


async def _serve_download_full(handler, abspath, file_size, user_key):
    handler.set_header("Content-Length", str(file_size))
    if handler.request.method == "HEAD":
        return
    if await _try_sendfile_download(handler, abspath, 0, file_size, user_key):
        return
    await _stream_chunks_to_handler(
        handler, MMapFileHandler.serve_file_chunk(abspath), user_key
    )


async def _serve_download_body(handler, abspath, filename, user_key):
    handler.set_header("Content-Disposition", f'attachment; filename="{filename}"')
    mime_type, _ = mimetypes.guess_type(abspath)
    mime_type = mime_type or APPLICATION_OCTET_STREAM
    handler.set_header("Content-Type", mime_type)

    try:
        file_size = os.path.getsize(abspath)
    except OSError:
        handler.set_status(404)
        handler.write("File not found")
        return

    handler.set_header("Accept-Ranges", "bytes")
    byte_range = parse_range_header(handler.request.headers.get("Range"), file_size)
    encoding = _download_wants_compression(handler, abspath, mime_type, file_size, byte_range)

    if byte_range:
        await _serve_download_range(
            handler, abspath, byte_range, file_size, user_key, encoding
        )
        return

    if encoding:
        await _serve_download_compressed(handler, abspath, encoding, user_key)
        return

    await _serve_download_full(handler, abspath, file_size, user_key)


def _raw_mode_allows_same_origin_frame(mime_type: str, abspath: str) -> bool:
    """Inline PDF/image raw responses may be embedded in our own media viewer."""
    if mime_type.startswith("image/"):
        return True
    if mime_type == "application/pdf":
        return True
    return browser_media_kind(os.path.basename(abspath)) is not None


async def _serve_raw_mode(handler, abspath):
    """Serve raw file content (inline) for client-side consumption."""
    try:
        mime_type = APPLICATION_OCTET_STREAM
        try:
            guessed_type, _ = mimetypes.guess_type(abspath)
            if guessed_type:
                mime_type = guessed_type
        except Exception:
            logging.debug("mimetypes.guess_type failed for raw serve", exc_info=True)
        handler.set_header("Content-Type", mime_type)
        handler.set_header("Content-Disposition", "inline")
        if _raw_mode_allows_same_origin_frame(mime_type, abspath):
            # Default DENY blocks <iframe>/<embed> in media_view.html on same host.
            handler.set_header("X-Frame-Options", "SAMEORIGIN")
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
    except Exception:
        logging.exception("Error serving raw file")
        handler.set_status(500)
        handler.write("Error serving file")



def _request_file_base(handler) -> str:
    """Path portion of the current file request (no query string)."""
    return handler.request.path.split("?", 1)[0]


def _serve_file_view(handler, abspath, filename, user_root):
    """Render file view template (client-side fetch)."""
    file_size = get_file_size_safe(abspath)
    rel_path = os.path.relpath(abspath, user_root).replace("\\", "/")
    handler.render(
        "file.html",
        filename=filename,
        path=rel_path,
        lines=[],
        default_file_view_line_limit=constants_module.DEFAULT_FILE_VIEW_LINE_LIMIT,
        open_editor=handler.get_argument("open_editor", "False"),
        full_file_content="",
        file_size=file_size,
    )


def _serve_media_view(handler, abspath, filename, user_root, media_kind: str):
    """Render inline image/PDF viewer (browser-native display via ?mode=raw)."""
    file_size = get_file_size_safe(abspath)
    rel_path = os.path.relpath(abspath, user_root).replace("\\", "/")
    base = _request_file_base(handler)
    media_src = f"{base}?mode=raw"
    download_href = f"{base}?download=1"
    handler.render(
        "media_view.html",
        filename=filename,
        path=rel_path,
        media_kind=media_kind,
        media_src=media_src,
        download_href=download_href,
        file_size=file_size,
    )


class RootHandler(BaseHandler):
    def get(self):
        self.redirect("/files/")


class MainHandler(BaseHandler):
    def _render_directory_browse(self, path: str, abspath: str) -> None:
        decision = self.check_access("file.list", resource_path=path)
        if decision is not None and decision.is_deny:
            self.set_status(403)
            self.write(decision.reason)
            return

        files = get_files_in_directory(abspath)

        db_conn = self.db_conn
        user_root = get_user_root(self)
        if db_conn:
            augment_with_shared_status(
                files,
                path,
                self.get_service("share_service").list_shares(db_conn),
                db_conn=db_conn,
                root_dir=user_root,
                viewer_username=get_username_string_for_db(self),
                viewer_is_admin=self.is_admin_user(),
            )

        parent_path = os.path.dirname(path) if path else None
        flags_for_template = get_current_feature_flags()
        user_favorites: set[str] = set()
        if db_conn and flags_for_template.get("favorites"):
            username = get_username_string_for_db(self)
            if username:
                user_favorites = set(
                    self.get_service("favorites_service").get_favorites(
                        db_conn, username
                    )
                )
        tag_rules = list_resource_tags(db_conn) if db_conn else []
        tag_colors = get_tag_colors_map(db_conn) if db_conn else {}
        file_tags_map: dict[str, list[str]] = {}
        for f in files:
            rel = join_path(path, f["name"]) if path else f["name"]
            file_tags_map[f["name"]] = get_tags_for_path(tag_rules, rel)

        self.sync_upload_config_from_db()

        self.render(
            "browse.html",
            current_path=path,
            parent_path=parent_path,
            files=files,
            join_path=join_path,
            get_file_icon=get_file_icon,
            features=flags_for_template,
            max_file_size=constants_module.MAX_FILE_SIZE,
            large_file_threshold=constants_module.LARGE_FILE_THRESHOLD_BYTES,
            range_chunk_bytes=constants_module.RANGE_CHUNK_BYTES,
            range_upload_concurrency=constants_module.RANGE_UPLOAD_CONCURRENCY,
            range_download_concurrency=constants_module.RANGE_DOWNLOAD_CONCURRENCY,
            range_pipeline_depth=constants_module.RANGE_PIPELINE_DEPTH,
            ws_chunk_bytes=constants_module.WS_CHUNK_BYTES,
            user_favorites=user_favorites,
            file_tags_map=file_tags_map,
            tag_colors=tag_colors,
            tag_chip_style=tag_chip_inline_style,
        )

    async def _handle_file_path(self, path: str) -> None:
        user_root = get_user_root(self)
        abspath = os.path.abspath(os.path.join(user_root, path))

        if not is_within_root(abspath, user_root):
            self.set_status(403)
            self.write(
                "Access denied: You don't have permission to perform this action"
            )
            return

        if os.path.isdir(abspath):
            if self.request.method == "HEAD":
                self.set_status(405)
                self.write("HEAD not supported for directories")
                return
            self._render_directory_browse(path, abspath)
            return

        if os.path.isfile(abspath):
            await self.serve_file(self, abspath, user_root)
            return

        self.set_status(404)
        self.write(
            "File not found: The requested file may have been moved or deleted"
        )

    @tornado.web.authenticated
    async def get(self, path):
        await self._handle_file_path(path)

    @tornado.web.authenticated
    async def head(self, path):
        await self._handle_file_path(path)

    @staticmethod
    async def serve_file(handler, abspath, user_root=None):
        if user_root is None:
            user_root = get_user_root(handler)
        filename = os.path.basename(abspath)
        rel_path = os.path.relpath(abspath, user_root).replace("\\", "/")
        file_size = get_file_size_safe(abspath)

        if handler.get_argument("download", None):
            decision = handler.check_access(
                "file.download", resource_path=rel_path, resource_size=file_size
            )
            if decision is not None and decision.is_deny:
                handler.set_status(403)
                handler.write(decision.reason)
                return
            await _serve_download(handler, abspath, filename)
            return

        mode = handler.get_argument("mode", "view")
        decision = handler.check_access(
            "file.read", resource_path=rel_path, resource_size=file_size
        )
        if decision is not None and decision.is_deny:
            handler.set_status(403)
            handler.write(decision.reason)
            return

        if mode == "raw":
            await _serve_raw_mode(handler, abspath)
            return
        media_kind = browser_media_kind(filename)
        if media_kind:
            _serve_media_view(handler, abspath, filename, user_root, media_kind)
            return
        _serve_file_view(handler, abspath, filename, user_root)


class EditViewHandler(BaseHandler):
    @tornado.web.authenticated
    @require_action("file.write", resource_arg="path")
    async def get(self, path):
        if not self.require_feature(
            "file_edit",
            True,
            body="Feature disabled: File editing is currently disabled by administrator",
        ):
            return

        user_root = get_user_root(self)
        abspath = os.path.abspath(os.path.join(user_root, path))
        if not is_within_root(abspath, user_root):
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

        self.render(
            "edit.html",
            filename=filename,
            path=path,
            full_file_content=full_file_content,
            total_lines=total_lines,
            features=get_current_feature_flags(),
        )


class CloudProviderMixin:
    def get_cloud_provider(self, provider_name: str):
        manager: CloudManager = self.application.settings.get(
            "cloud_manager", CLOUD_MANAGER
        )
        provider = manager.get(provider_name)
        if not provider:
            self.set_status(404)
            self.write({"error": PROVIDER_NOT_CONFIGURED})
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
        except Exception as exc:
            self.handle_cloud_error(
                exc,
                f"Failed to list cloud files for provider {provider_name}",
                "Failed to load cloud files",
            )
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
        except Exception as exc:
            self.handle_cloud_error(
                exc,
                f"Failed to download cloud file from {provider_name}",
                "Failed to download cloud file",
            )
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
        self.render("error.html", status_code=404, error_message="Page not found")


class TaggedFilesHandler(BaseHandler):
    """Browse all files that match a given ABAC resource tag."""

    @tornado.web.authenticated
    def get(self, tag_name: str):
        db_conn = self.db_conn
        rules = list_resource_tags(db_conn) if db_conn else []
        patterns = [r["glob_pattern"] for r in rules if r.get("tag") == tag_name]
        all_tags = sorted({r["tag"] for r in rules if r.get("tag")})

        entries: list[dict] = []
        file_count = 0
        folder_count = 0
        if patterns:
            root = get_user_root(self)
            for path in get_files_by_tag_patterns(patterns, root):
                is_dir = path.endswith("/")
                if is_dir:
                    folder_count += 1
                    name = path.rstrip("/")
                else:
                    file_count += 1
                    name = path
                entries.append({"path": path, "name": name, "is_dir": is_dir})

        tag_colors = get_tag_colors_map(db_conn) if db_conn else {}
        self.render(
            "tagged_files.html",
            tag_name=tag_name,
            patterns=patterns,
            entries=entries,
            file_count=file_count,
            folder_count=folder_count,
            all_tags=all_tags,
            tag_colors=tag_colors,
            tag_chip_style=tag_chip_inline_style,
            user=self.current_user,
            get_file_icon=get_file_icon,
        )


class NoCacheStaticFileHandler(tornado.web.StaticFileHandler):
    def set_extra_headers(self, path):
        self.set_header("Cache-Control", "no-cache, no-store, must-revalidate")
        self.set_header("Pragma", "no-cache")
        self.set_header("Expires", "0")
