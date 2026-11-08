"""Content hashes for static files.

Tornado's static handler versions a file with SHA-512 of its bytes. Templates,
the import map, and the service worker all use that same digest so a cached
response is the current file.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

_STATIC_ROOT = Path(__file__).resolve().parent / "static"
_HASHED_SUFFIXES = {
    ".js",
    ".mjs",
    ".css",
    ".png",
    ".svg",
    ".webp",
    ".gif",
    ".jpg",
    ".jpeg",
    ".ico",
    ".woff",
    ".woff2",
}
_SKIP = {"js/sw.js"}
_MODULE_SUFFIXES = {".js", ".mjs"}
_WORKER_KEYS = (
    "js/transfer-engine/worker.js",
    "js/transfer-engine/compress-worker.js",
)
_WARM_EXACT = (
    "css/app.css",
    "css/chat.css",
    "css/collab.css",
    "css/gitlab.css",
    "favicon.png",
    "img/logo-icon.png",
    "img/apple-touch-icon.png",
    "img/pwa-icon-192.png",
    "img/pwa-icon-512.png",
    "js/aird-core.js",
    "js/theme.js",
    "js/transfer-tracker.js",
    "js/pwa.js",
    "js/common/command-palette.js",
    "js/feature-flags-live.js",
    "js/chat-ws.js",
    "js/chat-e2e.js",
    "js/chat-notify.js",
    "js/chat-attach.js",
    "js/chat-secret.js",
    "js/chat-key-backup.js",
    "js/collab-ui.js",
    "js/collab-api.js",
    "js/chat-collab-quick.js",
    "js/chat-collab-mentions.js",
    "js/runtime-config.js",
    "js/transfer-background.js",
    "js/file-transfer-http.js",
    "js/file-transfer-ws.js",
    "js/transfer-manager.js",
    "js/download-manager.js",
    "js/folder-size-scan.js",
    "js/components/folder-picker.js",
    "js/webauthn.js",
    "js/pages/chat.js",
    "js/pages/super-search.js",
    "js/pages/polls.js",
    "js/pages/lists.js",
    "js/pages/meet.js",
    "js/pages/meet-join.js",
    "js/pages/p2p-page.js",
    "js/share/app.js",
    "js/vendor/qrcode-browser.js",
    "js/vendor/fflate.js",
)
_WARM_PREFIXES = (
    "fonts/",
    "js/browse/",
    "js/onedrive-fe/",
    "js/core/",
    "js/transfer-engine/",
)

@dataclass(frozen=True)
class _Index:
    files: dict[str, str]
    build_id: str
    import_map_json: str
    worker_json: str
    warm: list[str]
    fonts: dict[str, str]


_index: _Index | None = None


def clear_asset_cache() -> None:
    global _index
    _index = None


def static_root() -> Path:
    return _STATIC_ROOT


def _digest(path: Path) -> str:
    hasher = hashlib.sha512()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def _rel_path(path: str) -> str:
    rel = path.lstrip("/")
    if rel.startswith("static/"):
        rel = rel[len("static/") :]
    return rel


def _walk_files() -> dict[str, str]:
    files: dict[str, str] = {}
    root = _STATIC_ROOT
    if not root.is_dir():
        return files
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        if path.suffix.lower() not in _HASHED_SUFFIXES:
            continue
        rel = path.relative_to(root).as_posix()
        if rel in _SKIP:
            continue
        try:
            files[rel] = _digest(path)
        except OSError:
            continue
    return files


def _build() -> _Index:
    files = _walk_files()
    lines = [f"{rel}\t{files[rel]}" for rel in sorted(files)]
    build_id = hashlib.sha256("\n".join(lines).encode("utf-8")).hexdigest()[:16]
    imports = {
        f"/static/{rel}": f"/static/{rel}?v={digest}"
        for rel, digest in files.items()
        if Path(rel).suffix.lower() in _MODULE_SUFFIXES
    }
    import_map_json = json.dumps({"imports": imports}, separators=(",", ":")).replace("<", "\\u003c")
    workers = {
        rel: f"/static/{rel}?v={files[rel]}"
        for rel in _WORKER_KEYS
        if rel in files
    }
    worker_json = json.dumps(workers, separators=(",", ":")).replace("<", "\\u003c")
    warm: list[str] = []
    fonts: dict[str, str] = {}
    for rel, digest in files.items():
        if rel.startswith("fonts/") and Path(rel).suffix.lower() in {".woff", ".woff2"}:
            fonts[f"/static/{rel}"] = digest
        if rel in _WARM_EXACT or rel.startswith(_WARM_PREFIXES):
            warm.append(f"/static/{rel}?v={digest}")
    warm.sort()
    return _Index(
        files=files,
        build_id=build_id,
        import_map_json=import_map_json,
        worker_json=worker_json,
        warm=warm,
        fonts=fonts,
    )


def get_index() -> _Index:
    global _index
    if _index is None:
        _index = _build()
    return _index


def file_hash(path: str) -> str:
    rel = _rel_path(path)
    cached = get_index().files.get(rel)
    if cached:
        return cached
    abs_path = _STATIC_ROOT / rel
    if not abs_path.is_file():
        return ""
    return _digest(abs_path)


def static_url(path: str) -> str:
    """Return ``/static/<path>?v=<sha512>`` for a file under ``aird/static``."""
    rel = _rel_path(path)
    digest = get_index().files.get(rel) or file_hash(rel)
    url = f"/static/{rel}"
    if not digest:
        return url
    return f"{url}?v={digest}"


def manifest_payload() -> dict[str, object]:
    index = get_index()
    return {
        "build": index.build_id,
        "files": index.files,
        "fonts": index.fonts,
        "warm": index.warm,
    }


def template_assets() -> dict[str, object]:
    index = get_index()
    return {
        "static_url": static_url,
        "asset_build_id": index.build_id,
        "asset_import_map_json": index.import_map_json,
        "asset_worker_json": index.worker_json,
    }
