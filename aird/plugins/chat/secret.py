"""Enhanced Secure Chats.

A room relays ciphertext and acknowledgements only while both people are
connected. Private keys never reach this process. Workers share the waiting
room so the other person can accept; message contents are not stored.
"""

from __future__ import annotations

import json
import os
import secrets
import sqlite3
import threading
import time
from dataclasses import dataclass, field

from aird.plugins.chat.e2e import parse_e2e_payload, validate_public_jwk

_GRACE_SEC = 20
_WAIT_SEC = 15 * 60


@dataclass
class EnhancedSecureRoom:
    room_id: str
    host: str
    peer: str
    pubs: dict[str, dict] = field(default_factory=dict)
    created: float = field(default_factory=time.monotonic)

    def other(self, username: str) -> str:
        return self.peer if username == self.host else self.host

    def has(self, username: str) -> bool:
        return username in (self.host, self.peer)

    def ready(self) -> bool:
        return self.host in self.pubs and self.peer in self.pubs


def _view(room: EnhancedSecureRoom) -> dict:
    return {
        "room_id": room.room_id,
        "host": room.host,
        "peer": room.peer,
        "pubs": dict(room.pubs),
        "ready": room.ready(),
    }


_SHARED_SCHEMA = """
CREATE TABLE IF NOT EXISTS rooms (
    room_id TEXT PRIMARY KEY,
    host TEXT NOT NULL,
    peer TEXT NOT NULL,
    pair_key TEXT NOT NULL UNIQUE,
    host_epk TEXT,
    peer_epk TEXT,
    created REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS outbox (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT NOT NULL,
    payload TEXT NOT NULL,
    created REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS presence (
    username TEXT PRIMARY KEY,
    seen REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_enhanced_outbox_user ON outbox (username);
"""


class EnhancedSecureHub:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._rooms: dict[str, EnhancedSecureRoom] = {}
        self._pair: dict[tuple[str, str], str] = {}
        self._offline: dict[str, int] = {}
        self._db_path: str | None = None
        self._conn: sqlite3.Connection | None = None

    def use_shared(self, path: str) -> None:
        self._db_path = path

    def shares_state(self) -> bool:
        return self._db_path is not None

    def _db(self) -> sqlite3.Connection:
        if self._conn is None:
            folder = os.path.dirname(self._db_path or "")
            if folder:
                os.makedirs(folder, exist_ok=True)
            conn = sqlite3.connect(self._db_path, check_same_thread=False, timeout=5)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA busy_timeout=3000")
            conn.executescript(_SHARED_SCHEMA)
            conn.commit()
            self._conn = conn
        return self._conn

    def enqueue(self, username: str, payload: dict) -> None:
        if not self._db_path or not username:
            return
        with self._lock:
            db = self._db()
            db.execute(
                "INSERT INTO outbox (username, payload, created) VALUES (?, ?, ?)",
                (username, json.dumps(payload), time.time()),
            )
            db.commit()

    def take_outbox(self, usernames: set[str]) -> list[tuple[str, dict]]:
        if not self._db_path or not usernames:
            return []
        names = tuple(usernames)
        with self._lock:
            db = self._db()
            self._sweep_shared(db, time.time())
            marks = ",".join("?" * len(names))
            rows = db.execute(
                f"SELECT id, username, payload FROM outbox WHERE username IN ({marks}) ORDER BY id",
                names,
            ).fetchall()
            if not rows:
                db.commit()
                return []
            ids = [int(row["id"]) for row in rows]
            id_marks = ",".join("?" * len(ids))
            db.execute(f"DELETE FROM outbox WHERE id IN ({id_marks})", ids)
            db.commit()
        notes: list[tuple[str, dict]] = []
        for row in rows:
            try:
                notes.append((str(row["username"]), json.loads(row["payload"])))
            except (TypeError, ValueError):
                continue
        return notes

    def touch_presence(self, usernames: set[str]) -> None:
        if not self._db_path or not usernames:
            return
        now = time.time()
        with self._lock:
            db = self._db()
            db.executemany(
                "INSERT INTO presence (username, seen) VALUES (?, ?) "
                "ON CONFLICT(username) DO UPDATE SET seen=excluded.seen",
                [(name, now) for name in usernames],
            )
            db.commit()

    def recently_online(self, username: str, window: float = 12.0) -> bool:
        if not self._db_path or not username:
            return False
        with self._lock:
            row = self._db().execute(
                "SELECT seen FROM presence WHERE username=?", (username,)
            ).fetchone()
        return row is not None and (time.time() - float(row["seen"])) <= window

    def invite(self, host: str, peer: str) -> dict:
        if not host or not peer or host == peer:
            raise ValueError("Choose someone else")
        if self._db_path:
            return self._invite_shared(host, peer)
        key = tuple(sorted((host, peer)))
        with self._lock:
            self._sweep_waiting()
            existing = self._rooms.get(self._pair.get(key, ""))
            if existing:
                return _view(existing)
            room = EnhancedSecureRoom(room_id=secrets.token_urlsafe(18), host=host, peer=peer)
            self._rooms[room.room_id] = room
            self._pair[key] = room.room_id
            return _view(room)

    def join(self, username: str, room_id: str, epk) -> dict:
        pub = validate_public_jwk(epk)
        if self._db_path:
            return self._join_shared(username, room_id, pub)
        with self._lock:
            room = self._rooms.get(room_id)
            if room is None or not room.has(username):
                raise ValueError("Enhanced Secure Chat is not available")
            room.pubs[username] = pub
            return _view(room)

    def relay(self, username: str, room_id: str, payload) -> tuple[str, dict]:
        blob = parse_e2e_payload(payload)
        if self._db_path:
            return self._peer_shared(username, room_id)[0], blob
        with self._lock:
            room = self._rooms.get(room_id)
            if room is None or not room.has(username) or not room.ready():
                raise ValueError("Enhanced Secure Chat is not open")
            return room.other(username), blob

    def peer_for(self, username: str, room_id: str) -> str:
        if self._db_path:
            return self._peer_shared(username, room_id)[0]
        with self._lock:
            room = self._rooms.get(room_id)
            if room is None or not room.has(username) or not room.ready():
                raise ValueError("Enhanced Secure Chat is not open")
            return room.other(username)

    def close(self, username: str, room_id: str) -> dict | None:
        if self._db_path:
            return self._close_shared(username, room_id)
        with self._lock:
            room = self._rooms.get(room_id)
            if room is None or not room.has(username):
                return None
            view = _view(room)
            self._forget(room)
            return view

    def drop_user(self, username: str) -> list[tuple[str, str]]:
        if self._db_path:
            return self._drop_shared(username)
        with self._lock:
            ended: list[tuple[str, str]] = []
            for room in tuple(self._rooms.values()):
                if room.has(username):
                    ended.append((room.room_id, room.other(username)))
                    self._forget(room)
            return ended

    def pending_for(self, username: str) -> list[dict]:
        if self._db_path:
            return self._pending_shared(username)
        with self._lock:
            self._sweep_waiting()
            notes = []
            for room in self._rooms.values():
                if not room.has(username):
                    continue
                if room.peer == username and username not in room.pubs:
                    notes.append({
                        "type": "enhanced_request",
                        "room_id": room.room_id,
                        "from": room.host,
                        "epk": room.pubs.get(room.host),
                    })
                elif room.ready():
                    notes.append({
                        "type": "enhanced_open",
                        "room_id": room.room_id,
                        "peer": room.other(username),
                        "epk": room.pubs.get(room.other(username)),
                    })
            return notes

    def mark_offline(self, username: str) -> int:
        with self._lock:
            token = self._offline.get(username, 0) + 1
            self._offline[username] = token
            return token

    def clear_offline(self, username: str) -> None:
        with self._lock:
            self._offline[username] = self._offline.get(username, 0) + 1

    def offline_token(self, username: str) -> int:
        with self._lock:
            return self._offline.get(username, 0)

    def _sweep_waiting(self) -> None:
        now = time.monotonic()
        for room in tuple(self._rooms.values()):
            if not room.ready() and (now - room.created) > _WAIT_SEC:
                self._forget(room)

    def _forget(self, room: EnhancedSecureRoom) -> None:
        self._rooms.pop(room.room_id, None)
        self._pair.pop(tuple(sorted((room.host, room.peer))), None)

    def _invite_shared(self, host: str, peer: str) -> dict:
        key = "\0".join(sorted((host, peer)))
        with self._lock:
            db = self._db()
            self._sweep_shared(db, time.time())
            row = db.execute("SELECT * FROM rooms WHERE pair_key=?", (key,)).fetchone()
            if row is None:
                room_id = secrets.token_urlsafe(18)
                db.execute(
                    "INSERT INTO rooms (room_id, host, peer, pair_key, created) VALUES (?, ?, ?, ?, ?)",
                    (room_id, host, peer, key, time.time()),
                )
                db.commit()
                row = db.execute("SELECT * FROM rooms WHERE room_id=?", (room_id,)).fetchone()
            return self._row_view(row)

    def _join_shared(self, username: str, room_id: str, pub: dict) -> dict:
        with self._lock:
            db = self._db()
            row = db.execute("SELECT * FROM rooms WHERE room_id=?", (room_id,)).fetchone()
            if row is None or username not in (row["host"], row["peer"]):
                raise ValueError("Enhanced Secure Chat is not available")
            column = "host_epk" if username == row["host"] else "peer_epk"
            db.execute(
                f"UPDATE rooms SET {column}=? WHERE room_id=?",
                (json.dumps(pub), room_id),
            )
            db.commit()
            row = db.execute("SELECT * FROM rooms WHERE room_id=?", (room_id,)).fetchone()
            return self._row_view(row)

    def _peer_shared(self, username: str, room_id: str) -> tuple[str, dict]:
        with self._lock:
            row = self._db().execute(
                "SELECT * FROM rooms WHERE room_id=?", (room_id,)
            ).fetchone()
            if row is None or username not in (row["host"], row["peer"]):
                raise ValueError("Enhanced Secure Chat is not open")
            view = self._row_view(row)
            if not view["ready"]:
                raise ValueError("Enhanced Secure Chat is not open")
            other = view["peer"] if username == view["host"] else view["host"]
            return other, view

    def _close_shared(self, username: str, room_id: str) -> dict | None:
        with self._lock:
            db = self._db()
            row = db.execute("SELECT * FROM rooms WHERE room_id=?", (room_id,)).fetchone()
            if row is None or username not in (row["host"], row["peer"]):
                return None
            view = self._row_view(row)
            db.execute("DELETE FROM rooms WHERE room_id=?", (room_id,))
            db.commit()
            return view

    def _drop_shared(self, username: str) -> list[tuple[str, str]]:
        with self._lock:
            db = self._db()
            rows = db.execute(
                "SELECT * FROM rooms WHERE host=? OR peer=?", (username, username)
            ).fetchall()
            ended = []
            for row in rows:
                other = row["peer"] if username == row["host"] else row["host"]
                ended.append((str(row["room_id"]), str(other)))
            if rows:
                db.execute("DELETE FROM rooms WHERE host=? OR peer=?", (username, username))
                db.commit()
            return ended

    def _pending_shared(self, username: str) -> list[dict]:
        with self._lock:
            db = self._db()
            self._sweep_shared(db, time.time())
            db.commit()
            rows = db.execute(
                "SELECT * FROM rooms WHERE host=? OR peer=?", (username, username)
            ).fetchall()
        notes = []
        for row in rows:
            view = self._row_view(row)
            if view["peer"] == username and username not in view["pubs"]:
                notes.append({
                    "type": "enhanced_request",
                    "room_id": view["room_id"],
                    "from": view["host"],
                    "epk": view["pubs"].get(view["host"]),
                })
            elif view["ready"]:
                other = view["peer"] if username == view["host"] else view["host"]
                notes.append({
                    "type": "enhanced_open",
                    "room_id": view["room_id"],
                    "peer": other,
                    "epk": view["pubs"].get(other),
                })
        return notes

    def _sweep_shared(self, db: sqlite3.Connection, now: float) -> None:
        cutoff = now - _WAIT_SEC
        db.execute("DELETE FROM rooms WHERE created < ? AND (host_epk IS NULL OR peer_epk IS NULL)", (cutoff,))
        db.execute("DELETE FROM outbox WHERE created < ?", (cutoff,))

    def _row_view(self, row: sqlite3.Row) -> dict:
        pubs: dict[str, dict] = {}
        if row["host_epk"]:
            pubs[str(row["host"])] = json.loads(row["host_epk"])
        if row["peer_epk"]:
            pubs[str(row["peer"])] = json.loads(row["peer_epk"])
        return {
            "room_id": str(row["room_id"]),
            "host": str(row["host"]),
            "peer": str(row["peer"]),
            "pubs": pubs,
            "ready": len(pubs) == 2,
        }


_hub = EnhancedSecureHub()


def get_enhanced_secure_hub() -> EnhancedSecureHub:
    if _hub._db_path is None:
        try:
            from aird.database.db import get_data_dir

            _hub.use_shared(os.path.join(get_data_dir(), "enhanced-secure.sqlite3"))
        except Exception:
            pass
    return _hub


def grace_seconds() -> int:
    return _GRACE_SEC
