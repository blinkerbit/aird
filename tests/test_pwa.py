"""Tests for PWA / Web Push helpers."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path


def test_vapid_keys_persist(tmp_path, monkeypatch):
    monkeypatch.setattr("aird.constants.ROOT_DIR", str(tmp_path))
    from aird.pwa.vapid import clear_vapid_cache, load_or_create_vapid, vapid_public_key

    clear_vapid_cache()
    a = load_or_create_vapid(str(tmp_path))
    assert a["public_key"]
    assert "BEGIN" in a["private_pem"]
    from aird.pwa.vapid import vapid_private_key_for_webpush

    raw = vapid_private_key_for_webpush(a["private_pem"])
    assert raw and "BEGIN" not in raw
    clear_vapid_cache()
    b = load_or_create_vapid(str(tmp_path))
    assert a["public_key"] == b["public_key"]
    assert vapid_public_key(str(tmp_path)) == a["public_key"]
    assert (tmp_path / ".aird" / "secrets" / "vapid.json").is_file()


def test_push_subscription_store(tmp_path):
    from aird.pwa import store as push_store

    conn = sqlite3.connect(str(tmp_path / "t.db"))
    push_store.upsert_subscription(
        conn,
        username="alice",
        endpoint="https://push.example/1",
        p256dh="p",
        auth="a",
    )
    subs = push_store.subscriptions_for_user(conn, "alice")
    assert len(subs) == 1
    assert subs[0]["endpoint"] == "https://push.example/1"
    push_store.delete_subscription(conn, endpoint="https://push.example/1", username="alice")
    assert push_store.subscriptions_for_user(conn, "alice") == []


def test_manifest_and_sw_files_exist():
    root = Path(__file__).resolve().parents[1] / "aird" / "static"
    assert (root / "manifest.webmanifest").is_file()
    assert (root / "js" / "sw.js").is_file()
    assert (root / "img" / "pwa-icon-192.png").is_file()
    assert (root / "img" / "pwa-icon-512.png").is_file()
    data = json.loads((root / "manifest.webmanifest").read_text(encoding="utf-8"))
    assert data["display"] == "standalone"
    assert data["start_url"] == "/static/pwa-shell.html"
    assert data["scope"] == "/"
    assert (root / "pwa-shell.html").is_file()
    worker = (root / "js" / "sw.js").read_text(encoding="utf-8")
    assert "aird-static-v" in worker
    assert "function cacheFirst" in worker
    assert "function networkOnly" in worker
    assert "isHashedStatic" in worker
    assert "staleWhileRevalidate" not in worker
    assert "aird-clear-pages" in worker
    assert "aird-warm" in worker
    assert "path.startsWith('/api/')" in worker
    install = worker.split("addEventListener('install'")[1].split("addEventListener('activate'")[0]
    assert "skipWaiting" not in install
    page = (root / "js" / "pwa.js").read_text(encoding="utf-8")
    assert "30 * 60 * 1000" in page
    assert "aird-build" in page
    assert "hasActiveWork" in page
    assert "dataset.airdCall" in page


def test_static_url_is_content_hash():
    import hashlib

    from aird.assets import clear_asset_cache, file_hash, static_url

    clear_asset_cache()
    url = static_url("js/theme.js")
    assert url.startswith("/static/js/theme.js?v=")
    version = url.split("?v=", 1)[1]
    assert version == file_hash("js/theme.js")
    digest = hashlib.sha512((Path(__file__).resolve().parents[1] / "aird" / "static" / "js" / "theme.js").read_bytes()).hexdigest()
    assert version == digest
    assert static_url("js/theme.js") == url
    assert len(version) == 128


def test_import_map_covers_bare_module_and_warm_skips_livekit():
    from aird.assets import clear_asset_cache, get_index

    clear_asset_cache()
    index = get_index()
    imports = json.loads(index.import_map_json)["imports"]
    bare = "/static/js/pages/chat.js"
    assert bare in imports
    assert imports[bare] == f"{bare}?v={index.files['js/pages/chat.js']}"
    assert "20261108" not in imports[bare]
    assert any(url.startswith("/static/css/app.css?v=") for url in index.warm)
    assert all("livekit-client" not in url for url in index.warm)
    assert all("swagger-ui" not in url for url in index.warm)
    workers = json.loads(index.worker_json)
    assert workers["js/transfer-engine/worker.js"].startswith("/static/js/transfer-engine/worker.js?v=")


def test_templates_use_hashed_static_helper():
    root = Path(__file__).resolve().parents[1] / "aird" / "templates"
    chat = (root / "chat.html").read_text(encoding="utf-8")
    assert "static_url('css/collab.css')" in chat
    assert "collab.css?v=" not in chat
    browse = (root / "browse.html").read_text(encoding="utf-8")
    assert 'type="importmap"' not in browse
    early = (root / "_theme_early.html").read_text(encoding="utf-8")
    assert 'name="aird-build"' in early
    assert 'type="importmap"' in early
    js = (Path(__file__).resolve().parents[1] / "aird" / "static" / "js" / "pages" / "chat.js").read_text(encoding="utf-8")
    assert "chat-calls.js?v=" not in js
    assert "from '../chat-calls.js'" in js or "import('../chat-calls.js')" in js
