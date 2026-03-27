"""P2P file transfer handlers using WebRTC signaling."""

import json
import logging
import secrets
import time
from typing import Dict, Optional

import tornado.web
import tornado.websocket

from aird.handlers.base_handler import BaseHandler, authenticate_handler
from aird.core.events import TransferStartedEvent, now_ts
from aird.core.security import is_valid_websocket_origin
from aird.utils.util import is_feature_enabled
from aird.services.p2p_service import P2PSignalingService

logger = logging.getLogger(__name__)


class P2PRoom:
    """Represents a P2P transfer room/session."""

    def __init__(
        self,
        room_id: str,
        creator_id: str,
        allow_anonymous: bool = False,
        expiry_seconds: Optional[int] = None,
    ):
        self.room_id = room_id
        self.creator_id = creator_id
        self.created_at = time.time()
        # None = no time-based expiry (room lives until empty or server restart)
        self.expiry_seconds = expiry_seconds
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

    # Optional clamp when expiry_seconds is set (tests / internal use only; API does not set expiry)
    MIN_EXPIRY = 300
    MAX_EXPIRY = 604800

    def create_room(
        self,
        creator_id: str,
        allow_anonymous: bool = False,
        expiry_seconds: Optional[int] = None,
    ) -> P2PRoom:
        """Create a new room with a unique ID. expiry_seconds None = never expire by age."""
        if expiry_seconds is not None:
            expiry_seconds = max(self.MIN_EXPIRY, min(self.MAX_EXPIRY, expiry_seconds))
        room_id = secrets.token_urlsafe(64)
        while room_id in self.rooms:
            room_id = secrets.token_urlsafe(64)

        room = P2PRoom(
            room_id,
            creator_id,
            allow_anonymous=allow_anonymous,
            expiry_seconds=expiry_seconds,
        )
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
        """Remove rooms older than their individual expiry (only if expiry_seconds is set)."""
        now = time.time()
        to_remove = [
            room_id
            for room_id, room in self.rooms.items()
            if room.expiry_seconds is not None
            and now - room.created_at > room.expiry_seconds
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
                room_mgr = self.room_manager or room_manager
                room = room_mgr.get_room(room_id)
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
        self._room_manager: P2PRoomManager = self.settings.get(
            "room_manager", room_manager
        )
        self._event_bus = self.settings.get("event_bus")
        services = self.settings.get("services", {})
        self._service: P2PSignalingService = services.get(
            "p2p_signaling_service"
        ) or P2PSignalingService(self._room_manager)

    def get_current_user(self):
        """Authenticate user for WebSocket connection."""
        user = authenticate_handler(self)
        if user:
            return user
        user_cookie = self.get_secure_cookie("user")
        if not user_cookie:
            return None
        # Keep legacy websocket fallback: plain cookie username is treated as user.
        try:
            user_data = json.loads(user_cookie.decode("utf-8"))
            if isinstance(user_data, dict):
                return user_data
            return {"username": str(user_data), "role": "user"}
        except Exception:
            try:
                if isinstance(user_cookie, bytes):
                    username = user_cookie.decode("utf-8")
                elif isinstance(user_cookie, str):
                    username = user_cookie
                else:
                    return None
                return {"username": username, "role": "user"}
            except Exception as e:
                logger.error(f"Error parsing user cookie: {e}")
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
                room = self._room_manager.get_room(room_id)
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

            self.username, self.peer_id = self._service.make_anonymous_identity()

            logger.info(
                f"P2P WebSocket opened for anonymous user: {self.username}, peer_id: {self.peer_id}"
            )

            self.write_message(
                json.dumps(
                    self._service.connected_payload(
                        self.peer_id,
                        self.username,
                        is_anonymous=True,
                        pending_room=self.pending_room_id,
                    )
                )
            )
            return

        self.username = user.get("username", "anonymous")
        self.peer_id = self._service.make_user_peer_id()

        logger.info(
            f"P2P WebSocket opened for user: {self.username}, peer_id: {self.peer_id}"
        )

        # Send peer ID to client
        self.write_message(
            json.dumps(
                self._service.connected_payload(
                    self.peer_id, self.username, is_anonymous=False
                )
            )
        )

    def on_message(self, message: str):
        logger.info(f"P2P message received from {self.peer_id}: {message[:200]}")
        try:
            data = json.loads(message)
            msg_type = data.get("type")
            logger.info(f"Message type: {msg_type}")
            handlers = {
                "create_room": self._handle_create_room,
                "join_room": self._handle_join_room,
                "leave_room": lambda _data: self._handle_leave_room(),
                "offer": self._handle_offer,
                "answer": self._handle_answer,
                "ice_candidate": self._handle_ice_candidate,
                "file_info": self._handle_file_info,
                "restart_connection": lambda _data: self._handle_restart_connection(),
            }
            handler = handlers.get(msg_type)
            if not handler:
                logger.warning("Unknown message type: %s", msg_type)
                return
            handler(data)

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

        room = self._service.create_room(
            self.peer_id,
            allow_anonymous=allow_anonymous,
            file_info=data.get("file_info"),
        )
        room.add_peer(self.peer_id, self)
        self.room = room

        if room.file_info:
            logger.info(f"File info: {room.file_info}")

        response = {
            "type": "room_created",
            "room_id": room.room_id,
            "file_info": room.file_info,
            "allow_anonymous": room.allow_anonymous,
        }
        logger.info(f"Sending room_created response: {response}")
        self.write_message(json.dumps(response))
        if self._event_bus is not None:
            self._event_bus.publish(
                TransferStartedEvent(
                    room_id=room.room_id,
                    initiator=self.username or "unknown",
                    allow_anonymous=allow_anonymous,
                    started_at=now_ts(),
                )
            )
        self._service.log_room_creation(room.room_id, self.username, allow_anonymous)

    def _handle_join_room(self, data: dict):
        """Join an existing room."""
        room_id = data.get("room_id")
        if not room_id:
            self.write_message(
                json.dumps({"type": "error", "message": "Room ID required"})
            )
            return

        room = self._service.join_room(room_id)
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
            self._service.leave_room(self.room)

        self.room = None
        logger.info(f"User {self.username} left room {room_id}")

    def _handle_offer(self, data: dict):
        """Forward WebRTC offer to the other peer."""
        if not self.room:
            return

        other_peer = self.room.get_other_peer(self.peer_id)
        if other_peer:
            self._service.forward_to_other_peer(
                self.room,
                self.peer_id,
                {"type": "offer", "sdp": data.get("sdp"), "from_peer": self.peer_id},
            )

    def _handle_answer(self, data: dict):
        """Forward WebRTC answer to the other peer."""
        if not self.room:
            return

        other_peer = self.room.get_other_peer(self.peer_id)
        if other_peer:
            self._service.forward_to_other_peer(
                self.room,
                self.peer_id,
                {
                    "type": "answer",
                    "sdp": data.get("sdp"),
                    "from_peer": self.peer_id,
                },
            )

    def _handle_ice_candidate(self, data: dict):
        """Forward ICE candidate to the other peer."""
        if not self.room:
            return

        other_peer = self.room.get_other_peer(self.peer_id)
        if other_peer:
            self._service.forward_to_other_peer(
                self.room,
                self.peer_id,
                {
                    "type": "ice_candidate",
                    "candidate": data.get("candidate"),
                    "from_peer": self.peer_id,
                },
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

    def _handle_restart_connection(self):
        """Broadcast restart_connection to other peer so both can reconnect with new settings."""
        if not self.room:
            return

        other_peer = self.room.get_other_peer(self.peer_id)
        if other_peer:
            self._service.forward_to_other_peer(
                self.room,
                self.peer_id,
                {
                    "type": "restart_connection",
                    "from_peer": self.peer_id,
                    "username": self.username,
                },
            )
            logger.info(f"Restart connection requested by {self.username}")

    def on_close(self):
        logger.info(f"P2P WebSocket closed for peer: {self.peer_id}")
        self._handle_leave_room()

    def check_origin(self, origin: str) -> bool:
        return is_valid_websocket_origin(self, origin)
