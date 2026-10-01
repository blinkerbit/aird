"""In-memory Enhanced Secure Chats.

A room relays ciphertext and acknowledgements only while both people are
connected. Nothing is written to disk or the database, and private keys never
reach this process.
"""

from __future__ import annotations

import secrets
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


class EnhancedSecureHub:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._rooms: dict[str, EnhancedSecureRoom] = {}
        self._pair: dict[tuple[str, str], str] = {}
        self._offline: dict[str, int] = {}

    def invite(self, host: str, peer: str) -> dict:
        if not host or not peer or host == peer:
            raise ValueError("Choose someone else")
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
        with self._lock:
            room = self._rooms.get(room_id)
            if room is None or not room.has(username):
                raise ValueError("Enhanced Secure Chat is not available")
            room.pubs[username] = pub
            return _view(room)

    def relay(self, username: str, room_id: str, payload) -> tuple[str, dict]:
        blob = parse_e2e_payload(payload)
        with self._lock:
            room = self._rooms.get(room_id)
            if room is None or not room.has(username) or not room.ready():
                raise ValueError("Enhanced Secure Chat is not open")
            return room.other(username), blob

    def peer_for(self, username: str, room_id: str) -> str:
        with self._lock:
            room = self._rooms.get(room_id)
            if room is None or not room.has(username) or not room.ready():
                raise ValueError("Enhanced Secure Chat is not open")
            return room.other(username)

    def close(self, username: str, room_id: str) -> dict | None:
        with self._lock:
            room = self._rooms.get(room_id)
            if room is None or not room.has(username):
                return None
            view = _view(room)
            self._forget(room)
            return view

    def drop_user(self, username: str) -> list[tuple[str, str]]:
        with self._lock:
            ended: list[tuple[str, str]] = []
            for room in list(self._rooms.values()):
                if room.has(username):
                    ended.append((room.room_id, room.other(username)))
                    self._forget(room)
            return ended

    def pending_for(self, username: str) -> list[dict]:
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
        for room in list(self._rooms.values()):
            if not room.ready() and (now - room.created) > _WAIT_SEC:
                self._forget(room)

    def _forget(self, room: EnhancedSecureRoom) -> None:
        self._rooms.pop(room.room_id, None)
        self._pair.pop(tuple(sorted((room.host, room.peer))), None)


_hub = EnhancedSecureHub()


def get_enhanced_secure_hub() -> EnhancedSecureHub:
    return _hub


def grace_seconds() -> int:
    return _GRACE_SEC
