"""Virtual browse roots from admin-assigned host paths."""

from __future__ import annotations

import os
import sqlite3

from aird.core.browse_paths import (
    is_writable_rel,
    overlay_mount_entries,
    resolve_rel,
    sanitize_mount_name,
    write_blocked_reason,
)
from aird.core.security import is_within_root
from aird.db.schema import init_db
from aird.db.user_mounts import ALL_USERS, insert_path_mount, list_user_path_mounts


def test_sanitize_mount_name():
    assert sanitize_mount_name("Projects") == "Projects"
    assert sanitize_mount_name(".hidden") is None
    assert sanitize_mount_name("a/b") is None
    assert sanitize_mount_name(".aird-shares") is None
    assert sanitize_mount_name("") is None


def test_two_unconnected_hosts_at_root(tmp_path):
    disk_a = tmp_path / "diskA"
    disk_b = tmp_path / "diskB"
    data = tmp_path / "data"
    disk_a.mkdir()
    disk_b.mkdir()
    data.mkdir()
    (disk_a / "a.txt").write_text("a")
    (disk_b / "b.txt").write_text("b")
    mounts = [
        {"mount_name": "Alpha", "host_path": str(disk_a), "writable": True},
        {"mount_name": "Beta", "host_path": str(disk_b), "writable": True},
    ]
    names = {e["name"] for e in overlay_mount_entries([], mounts, "")}
    assert names == {"Alpha", "Beta"}
    assert overlay_mount_entries([], mounts, "Alpha") == []

    p, confine = resolve_rel(str(data), "Alpha/a.txt", mounts)
    assert p and os.path.isfile(p) and is_within_root(p, confine)
    p2, confine2 = resolve_rel(str(data), "Beta/b.txt", mounts)
    assert p2 and os.path.isfile(p2) and is_within_root(p2, confine2)
    personal = data / "mine.txt"
    personal.write_text("m")
    p3, confine3 = resolve_rel(str(data), "mine.txt", mounts)
    assert p3 and os.path.isfile(p3) and is_within_root(p3, confine3)


def test_traversal_blocked(tmp_path):
    host = tmp_path / "host"
    other = tmp_path / "other"
    data = tmp_path / "data"
    host.mkdir()
    other.mkdir()
    data.mkdir()
    (other / "secret.txt").write_text("no")
    mounts = [{"mount_name": "Safe", "host_path": str(host), "writable": True}]
    p, confine = resolve_rel(str(data), "Safe/../other/secret.txt", mounts)
    assert p is None and confine is None


def test_star_vs_user_override(tmp_path):
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    init_db(conn)
    shared = tmp_path / "shared"
    alice_only = tmp_path / "alice"
    shared.mkdir()
    alice_only.mkdir()
    insert_path_mount(
        conn,
        username=ALL_USERS,
        host_path=str(shared),
        mount_name="Team",
        writable=True,
    )
    insert_path_mount(
        conn,
        username="alice",
        host_path=str(alice_only),
        mount_name="Team",
        writable=False,
    )
    alice = list_user_path_mounts(conn, "alice")
    bob = list_user_path_mounts(conn, "bob")
    assert len(alice) == 1 and alice[0]["host_path"] == str(alice_only)
    assert not alice[0]["writable"]
    assert len(bob) == 1 and bob[0]["host_path"] == str(shared)
    assert bob[0]["writable"]


def test_write_blocked_mount_root_and_readonly(tmp_path):
    host = tmp_path / "ro"
    host.mkdir()
    mounts = [{"mount_name": "Docs", "host_path": str(host), "writable": False}]
    assert write_blocked_reason("Docs", mounts, as_target=True) == "assigned"
    assert write_blocked_reason("Docs", mounts, as_target=False) == "readonly"
    assert write_blocked_reason("Docs/file.txt", mounts) == "readonly"
    assert not is_writable_rel("Docs/file.txt", mounts)
    rw = [{"mount_name": "Docs", "host_path": str(host), "writable": True}]
    assert write_blocked_reason("Docs/file.txt", rw) is None
    assert write_blocked_reason("Docs", rw, as_target=True) == "assigned"
