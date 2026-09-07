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
    assert data["start_url"] == "/files/"
