"""P2P file transfer handlers using WebRTC signaling."""

import json
import logging
import secrets
import time
from typing import Dict, Optional

import tornado.web
import tornado.websocket

from aird.handlers.base_handler import BaseHandler
from aird.core.security import is_valid_websocket_origin
from aird.utils.util import is_feature_enabled

logger = logging.getLogger(__name__)


class P2PRoom:
    """Represents a P2P transfer room/session."""

    # Default expiry: 24 hours
    DEFAULT_EXPIRY = 86400

    def __init__(self, room_id: str, creator_id: str, allow_anonymous: bool = False, expiry_seconds: int = None):
        self.room_id = room_id
        self.creator_id = creator_id
        self.created_at = time.time()
        self.expiry_seconds = expiry_seconds if expiry_seconds is not None else self.DEFAULT_EXPIRY
        self.peers: Dict[str, "P2PSignalingHandler"] = {}
        self.file_info: Optional[dict] = None  # Info about file being shared
        self.allow_anonymous = allow_anonymous  # Whether anonymous users can join

    def add_peer(self, peer_id: str, handler: "P2PSignalingHandler"):
        self.peers[peer_id] = handler

    def remove_peer(self, peer_id: str):
        self.peers.pop(peer_id, None)

    def get_other_peer(self, peer_id: str) -> Optional["P2PSignalingHandler"]:
        """Get the other peer in the room (for 1:1 transfers)."""
        for pid, handler in self.peers.items():
            if pid != peer_id:
                return handler
        return None

    def broadcast(self, message: dict, exclude_peer: str = None):
        """Send message to all peers except the excluded one."""
        for peer_id, handler in self.peers.items():
            if peer_id != exclude_peer:
                try:
                    handler.write_message(json.dumps(message))
                except Exception as e:
                    logger.error(f"Error broadcasting to peer {peer_id}: {e}")


class P2PRoomManager:
    """Manages P2P transfer rooms."""

    def __init__(self):
        self.rooms: Dict[str, P2PRoom] = {}
        self._cleanup_interval = 3600  # 1 hour
        self._max_room_age = 86400  # 24 hours

    # Clamp range: 5 minutes to 7 days
    MIN_EXPIRY = 300
    MAX_EXPIRY = 604800

    def create_room(self, creator_id: str, allow_anonymous: bool = False, expiry_seconds: int = None) -> P2PRoom:
        """Create a new room with a unique ID."""
        if expiry_seconds is not None:
            expiry_seconds = max(self.MIN_EXPIRY, min(self.MAX_EXPIRY, expiry_seconds))
        room_id = secrets.token_urlsafe(64)
        while room_id in self.rooms:
            room_id = secrets.token_urlsafe(64)

        room = P2PRoom(room_id, creator_id, allow_anonymous=allow_anonymous, expiry_seconds=expiry_seconds)
        self.rooms[room_id] = room
        logger.info(f"Created P2P room: {room_id} (anonymous: {allow_anonymous})")
        return room

    def get_room(self, room_id: str) -> Optional[P2PRoom]:
        return self.rooms.get(room_id)

    def remove_room(self, room_id: str):
        if room_id in self.rooms:
            del self.rooms[room_id]
            logger.info(f"Removed P2P room: {room_id}")

    def cleanup_old_rooms(self):
        """Remove rooms older than their individual expiry."""
        now = time.time()
        to_remove = [
            room_id
            for room_id, room in self.rooms.items()
            if now - room.created_at > room.expiry_seconds
        ]
        for room_id in to_remove:
            self.remove_room(room_id)


# Global room manager instance
room_manager = P2PRoomManager()


class P2PTransferHandler(BaseHandler):
    """Handler for the P2P transfer page."""

    def get(self):
        if not self.require_feature(
            "p2p_transfer",
            True,
            body="Feature disabled: P2P Transfer is currently disabled by administrator",
        ):
            return

        room_id = self.get_argument("room", None)
        current_user = self.get_current_user()

        # If user is not logged in
        if not current_user:
            if room_id:
                room = room_manager.get_room(room_id)
                if not room or not room.allow_anonymous:
                    self.redirect(self.get_login_url())
                    return

            self.render(
                "p2p_transfer.html",
                room_id=room_id,
                current_user=None,
                is_anonymous=True,
            )
            return

        self.render(
            "p2p_transfer.html",
            room_id=room_id,
            current_user=current_user,
            is_anonymous=False,
        )


class P2PSignalingHandler(tornado.websocket.WebSocketHandler):
    """WebSocket handler for WebRTC signaling."""

    def initialize(self):
        self.peer_id: Optional[str] = None
        self.room: Optional[P2PRoom] = None
        self.username: Optional[str] = None
        self.is_anonymous: bool = False
        self.pending_room_id: Optional[str] = None  # Room to join for anonymous users

    def get_current_user(self):
        """Authenticate user for WebSocket connection."""
        user_cookie = self.get_secure_cookie("user")
        if user_cookie:
            try:
                # Try to parse as JSON first
                try:
                    user_data = json.loads(user_cookie.decode("utf-8"))
                    if isinstance(user_data, dict):
                        return user_data
                    # If it's a string from JSON, use it as username
                    return {"username": str(user_data), "role": "user"}
                except (json.JSONDecodeError, TypeError):
                    # If it's not JSON, treat as plain username
                    username = (
                        user_cookie.decode("utf-8")
                        if isinstance(user_cookie, bytes)
                        else str(user_cookie)
                    )
                    return {"username": username, "role": "user"}
            except Exception as e:
                logger.error(f"Error parsing user cookie: {e}")
                return None
        return None

    def open(self):
        logger.info("P2P WebSocket connection attempt")

        # Check if P2P transfer feature is enabled
        if not is_feature_enabled("p2p_transfer", True):
            logger.warning("P2P WebSocket: Feature is disabled")
            self.write_message(
                json.dumps(
                    {
                        "type": "error",
                        "message": "P2P Transfer is currently disabled by administrator",
                    }
                )
            )
            self.close(code=1008, reason="Feature disabled")
            return

        # Check for room_id parameter for anonymous access
        room_id = self.get_argument("room", None)

        user = self.get_current_user()
        if not user:
            self.is_anonymous = True
            if room_id:
                room = room_manager.get_room(room_id)
                if room and room.allow_anonymous:
                    self.pending_room_id = room_id
                else:
                    self.write_message(
                        json.dumps(
                            {
                                "type": "error",
                                "message": "This share link requires login. Please log in to receive the file.",
                            }
                        )
                    )
                    self.close(code=1008, reason="Authentication required for room")
                    return

            self.username = f"Guest_{secrets.token_hex(4)}"
            self.peer_id = secrets.token_urlsafe(64)

            logger.info(
                f"P2P WebSocket opened for anonymous user: {self.username}, peer_id: {self.peer_id}"
            )

            self.write_message(
                json.dumps(
                    {
                        "type": "connected",
                        "peer_id": self.peer_id,
                        "username": self.username,
                        "is_anonymous": True,
                        "pending_room": self.pending_room_id,
                    }
                )
            )
            return

        self.username = user.get("username", "anonymous")
        self.peer_id = secrets.token_urlsafe(64)

        logger.info(
            f"P2P WebSocket opened for user: {self.username}, peer_id: {self.peer_id}"
        )

        # Send peer ID to client
        self.write_message(
            json.dumps(
                {
                    "type": "connected",
                    "peer_id": self.peer_id,
                    "username": self.username,
                    "is_anonymous": False,
                }
            )
        )

    def on_message(self, message: str):
        logger.info(f"P2P message received from {self.peer_id}: {message[:200]}")
        try:
            data = json.loads(message)
            msg_type = data.get("type")
            logger.info(f"Message type: {msg_type}")

            if msg_type == "create_room":
                self._handle_create_room(data)
            elif msg_type == "join_room":
                self._handle_join_room(data)
            elif msg_type == "leave_room":
                self._handle_leave_room()
            elif msg_type == "offer":
                self._handle_offer(data)
            elif msg_type == "answer":
                self._handle_answer(data)
            elif msg_type == "ice_candidate":
                self._handle_ice_candidate(data)
            elif msg_type == "file_info":
                self._handle_file_info(data)
            else:
                logger.warning(f"Unknown message type: {msg_type}")

        except json.JSONDecodeError as e:
            logger.error(f"Invalid JSON received: {e}")
            self.write_message(json.dumps({"type": "error", "message": "Invalid JSON"}))
        except Exception as e:
            logger.error(f"Error handling message: {e}", exc_info=True)
            self.write_message(json.dumps({"type": "error", "message": str(e)}))

    def _handle_create_room(self, data: dict):
        """Create a new room and join it."""
        logger.info(f"Creating room for peer {self.peer_id}, user {self.username}")

        if self.room:
            self._handle_leave_room()

        # Check if anonymous access is requested
        allow_anonymous = data.get("allow_anonymous", False)

        # Parse custom expiry
        expiry_seconds = None
        expiry_minutes = data.get("expiry_minutes")
        if expiry_minutes is not None:
            try:
                expiry_seconds = int(expiry_minutes) * 60
            except (ValueError, TypeError):
                pass

        room = room_manager.create_room(self.peer_id, allow_anonymous=allow_anonymous, expiry_seconds=expiry_seconds)
        room.add_peer(self.peer_id, self)
        self.room = room

        # Store file info if provided
        if "file_info" in data:
            room.file_info = data["file_info"]
            logger.info(f"File info: {room.file_info}")

        response = {
            "type": "room_created",
            "room_id": room.room_id,
            "file_info": room.file_info,
            "allow_anonymous": room.allow_anonymous,
            "expiry_minutes": room.expiry_seconds // 60,
        }
        logger.info(f"Sending room_created response: {response}")
        self.write_message(json.dumps(response))
        logger.info(
            f"Room {room.room_id} created by {self.username} (anonymous: {allow_anonymous})"
        )

    def _handle_join_room(self, data: dict):
        """Join an existing room."""
        room_id = data.get("room_id")
        if not room_id:
            self.write_message(
                json.dumps({"type": "error", "message": "Room ID required"})
            )
            return

        room = room_manager.get_room(room_id)
        if not room:
            self.write_message(
                json.dumps({"type": "error", "message": "Room not found"})
            )
            return

        # Check if anonymous user can join this room
        if self.is_anonymous and not room.allow_anonymous:
            self.write_message(
                json.dumps(
                    {
                        "type": "error",
                        "message": "This share link requires login. Please log in to receive the file.",
                    }
                )
            )
            return

        if len(room.peers) >= 2:
            self.write_message(json.dumps({"type": "error", "message": "Room is full"}))
            return

        if self.room:
            self._handle_leave_room()

        room.add_peer(self.peer_id, self)
        self.room = room

        # Notify the joiner about room info
        self.write_message(
            json.dumps(
                {
                    "type": "room_joined",
                    "room_id": room.room_id,
                    "file_info": room.file_info,
                    "peer_count": len(room.peers),
                    "allow_anonymous": room.allow_anonymous,
                }
            )
        )

        # Notify existing peer about new joiner
        room.broadcast(
            {
                "type": "peer_joined",
                "peer_id": self.peer_id,
                "username": self.username,
                "is_anonymous": self.is_anonymous,
            },
            exclude_peer=self.peer_id,
        )

        logger.info(
            f"User {self.username} joined room {room_id} (anonymous: {self.is_anonymous})"
        )

    def _handle_leave_room(self):
        """Leave the current room."""
        if not self.room:
            return

        room_id = self.room.room_id
        self.room.remove_peer(self.peer_id)

        # Notify other peers
        self.room.broadcast(
            {"type": "peer_left", "peer_id": self.peer_id, "username": self.username}
        )

        # Clean up empty rooms
        if not self.room.peers:
            room_manager.remove_room(room_id)

        self.room = None
        logger.info(f"User {self.username} left room {room_id}")

    def _handle_offer(self, data: dict):
        """Forward WebRTC offer to the other peer."""
        if not self.room:
            return

        other_peer = self.room.get_other_peer(self.peer_id)
        if other_peer:
            other_peer.write_message(
                json.dumps(
                    {"type": "offer", "sdp": data.get("sdp"), "from_peer": self.peer_id}
                )
            )

    def _handle_answer(self, data: dict):
        """Forward WebRTC answer to the other peer."""
        if not self.room:
            return

        other_peer = self.room.get_other_peer(self.peer_id)
        if other_peer:
            other_peer.write_message(
                json.dumps(
                    {
                        "type": "answer",
                        "sdp": data.get("sdp"),
                        "from_peer": self.peer_id,
                    }
                )
            )

    def _handle_ice_candidate(self, data: dict):
        """Forward ICE candidate to the other peer."""
        if not self.room:
            return

        other_peer = self.room.get_other_peer(self.peer_id)
        if other_peer:
            other_peer.write_message(
                json.dumps(
                    {
                        "type": "ice_candidate",
                        "candidate": data.get("candidate"),
                        "from_peer": self.peer_id,
                    }
                )
            )

    def _handle_file_info(self, data: dict):
        """Update file info for the room."""
        if not self.room:
            return

        self.room.file_info = data.get("file_info")
        self.room.broadcast(
            {"type": "file_info_updated", "file_info": self.room.file_info},
            exclude_peer=self.peer_id,
        )

    def on_close(self):
        logger.info(f"P2P WebSocket closed for peer: {self.peer_id}")
        self._handle_leave_room()

    def check_origin(self, origin: str) -> bool:
        return is_valid_websocket_origin(self, origin)
