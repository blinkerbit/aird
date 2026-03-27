"""P2P signaling orchestration service."""

from __future__ import annotations

import json
import logging
import secrets
from typing import Any

logger = logging.getLogger(__name__)


class P2PSignalingService:
    """Encapsulates room operations and peer-to-peer forwarding behavior."""

    def __init__(self, room_manager: Any):
        self.room_manager = room_manager

    def connected_payload(
        self,
        peer_id: str,
        username: str,
        *,
        is_anonymous: bool,
        pending_room: str | None = None,
    ) -> dict[str, Any]:
        payload = {
            "type": "connected",
            "peer_id": peer_id,
            "username": username,
            "is_anonymous": is_anonymous,
            "pending_room": pending_room,
        }
        return payload

    def make_anonymous_identity(self) -> tuple[str, str]:
        return (f"Guest_{secrets.token_hex(4)}", secrets.token_urlsafe(64))

    def make_user_peer_id(self) -> str:
        return secrets.token_urlsafe(64)

    def create_room(
        self,
        creator_peer_id: str,
        allow_anonymous: bool,
        file_info: dict[str, Any] | None = None,
    ):
        room = self.room_manager.create_room(
            creator_peer_id, allow_anonymous=allow_anonymous
        )
        if file_info is not None:
            room.file_info = file_info
        return room

    def join_room(self, room_id: str):
        return self.room_manager.get_room(room_id)

    def leave_room(self, room):
        if room and not room.peers:
            self.room_manager.remove_room(room.room_id)

    def forward_to_other_peer(
        self, room, peer_id: str, payload: dict[str, Any]
    ) -> None:
        other_peer = room.get_other_peer(peer_id) if room else None
        if other_peer:
            other_peer.write_message(json.dumps(payload))

    def notify_peer_change(
        self, room, peer_id: str, username: str, *, joined: bool
    ) -> None:
        if not room:
            return
        room.broadcast(
            {
                "type": "peer_joined" if joined else "peer_left",
                "peer_id": peer_id,
                "username": username,
            },
            exclude_peer=peer_id if joined else None,
        )

    def log_room_creation(
        self, room_id: str, username: str, allow_anonymous: bool
    ) -> None:
        logger.info(
            "Room %s created by %s (anonymous: %s)",
            room_id,
            username,
            allow_anonymous,
        )
