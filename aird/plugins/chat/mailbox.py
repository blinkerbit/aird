"""Per-user chat mailbox SQLite files under ``.aird-chats/mailbox.sqlite3``."""

from __future__ import annotations

import logging
import os
import sqlite3
import threading
from collections import OrderedDict
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Iterator

from aird.constants import CHAT_STORE_FOLDER
from aird.core.user_storage import ensure_user_home_layout, user_home_for_username

logger = logging.getLogger(__name__)

MAILBOX_FILENAME = "mailbox.sqlite3"
MAX_OPEN = 32
MAX_GROUP_MEMBERS = 32
MAX_PINS = 5
LEGACY_NS = "11111111-1111-4111-8111-111111111111"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS conversations (
    id TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    title TEXT,
    muted INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS members (
    conversation_id TEXT NOT NULL,
    username TEXT NOT NULL,
    user_id INTEGER NOT NULL,
    joined_at TEXT NOT NULL,
    PRIMARY KEY (conversation_id, username)
);
CREATE TABLE IF NOT EXISTS messages (
    id TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL,
    sender_username TEXT NOT NULL,
    sender_id INTEGER NOT NULL,
    msg_type TEXT NOT NULL,
    body TEXT,
    metadata_json TEXT,
    reply_to_id TEXT,
    edited_at TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_messages_conv_created
    ON messages (conversation_id, created_at, id);
CREATE TABLE IF NOT EXISTS read_state (
    conversation_id TEXT PRIMARY KEY,
    last_read_id TEXT NOT NULL,
    last_read_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS receipts (
    conversation_id TEXT NOT NULL,
    username TEXT NOT NULL,
    last_read_id TEXT NOT NULL,
    last_read_at TEXT NOT NULL,
    PRIMARY KEY (conversation_id, username)
);
CREATE TABLE IF NOT EXISTS reactions (
    message_id TEXT NOT NULL,
    username TEXT NOT NULL,
    emoji TEXT NOT NULL,
    PRIMARY KEY (message_id, username)
);
CREATE TABLE IF NOT EXISTS pins (
    conversation_id TEXT NOT NULL,
    message_id TEXT NOT NULL,
    pinned_by TEXT NOT NULL,
    pinned_at TEXT NOT NULL,
    PRIMARY KEY (conversation_id, message_id)
);
"""


@dataclass
class Mailbox:
    username: str
    path: str
    conn: sqlite3.Connection
    lock: threading.RLock
    fts: bool = True


def mailbox_path_for(username: str) -> str:
    home = user_home_for_username(username)
    ensure_user_home_layout(home)
    folder = os.path.join(home, CHAT_STORE_FOLDER)
    import aird.constants as constants_module
    from aird.core.security import sanitize_username_for_folder

    if not constants_module.MULTI_USER:
        safe = sanitize_username_for_folder(username) or "user"
        folder = os.path.join(folder, safe)
    os.makedirs(folder, exist_ok=True)
    return os.path.join(folder, MAILBOX_FILENAME)


def _connect(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(path, check_same_thread=False, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


def init_schema(conn: sqlite3.Connection) -> bool:
    """Create tables. Return True if FTS5 is available."""
    conn.executescript(_SCHEMA)
    fts = True
    try:
        conn.execute(
            """
            CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts USING fts5(
                message_id UNINDEXED,
                body,
                original_name
            )
            """
        )
    except sqlite3.OperationalError:
        fts = False
        logger.debug("FTS5 unavailable for chat mailbox")
    conn.commit()
    return fts


class MailboxPool:
    def __init__(self, max_open: int = MAX_OPEN) -> None:
        self._max = max_open
        self._guard = threading.RLock()
        self._boxes: OrderedDict[str, Mailbox] = OrderedDict()

    def get(self, username: str) -> Mailbox:
        key = (username or "").strip()
        if not key:
            raise ValueError("username required")
        with self._guard:
            box = self._boxes.get(key)
            if box is not None:
                self._boxes.move_to_end(key)
                return box
            path = mailbox_path_for(key)
            conn = _connect(path)
            fts = init_schema(conn)
            box = Mailbox(username=key, path=path, conn=conn, lock=threading.RLock(), fts=fts)
            self._boxes[key] = box
            while len(self._boxes) > self._max:
                _, old = self._boxes.popitem(last=False)
                if old.username == key:
                    self._boxes[key] = old
                    break
                try:
                    old.conn.close()
                except sqlite3.Error:
                    pass
            return box

    def close_all(self) -> None:
        with self._guard:
            for box in self._boxes.values():
                try:
                    box.conn.close()
                except sqlite3.Error:
                    pass
            self._boxes.clear()


_pool = MailboxPool()


def get_pool() -> MailboxPool:
    return _pool


def reset_pool() -> None:
    """Tests: drop cached connections."""
    _pool.close_all()


@contextmanager
def mailbox(username: str) -> Iterator[Mailbox]:
    box = _pool.get(username)
    with box.lock:
        yield box
