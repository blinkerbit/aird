"""
Comprehensive test cases for P2P file transfer handlers.
"""

import pytest
import json
import time
from unittest.mock import MagicMock, patch, AsyncMock

from aird.handlers.p2p_handlers import (
    P2PRoom,
    P2PRoomManager,
    P2PTransferHandler,
    P2PSignalingHandler,
    room_manager,
)

from tests.handler_helpers import authenticate, prepare_handler


# =============================================================================
# P2PRoom Tests
# =============================================================================

class TestP2PRoom:
    """Test cases for P2PRoom class."""
    
    def test_initialization_default(self):
        """Test room initialization with default values."""
        room = P2PRoom("test_room_id", "creator_123")
        
        assert room.room_id == "test_room_id"
        assert room.creator_id == "creator_123"
        assert room.allow_anonymous is False
        assert room.file_info is None
        assert room.peers == {}
        assert room.created_at <= time.time()
    
    def test_initialization_with_anonymous(self):
        """Test room initialization with anonymous access enabled."""
        room = P2PRoom("test_room", "creator_456", allow_anonymous=True)
        
        assert room.allow_anonymous is True
    
    def test_add_peer(self):
        """Test adding a peer to the room."""
        room = P2PRoom("test_room", "creator")
        mock_handler = MagicMock()
        
        room.add_peer("peer_1", mock_handler)
        
        assert "peer_1" in room.peers
        assert room.peers["peer_1"] == mock_handler
    
    def test_add_multiple_peers(self):
        """Test adding multiple peers to the room."""
        room = P2PRoom("test_room", "creator")
        handler1 = MagicMock()
        handler2 = MagicMock()
        
        room.add_peer("peer_1", handler1)
        room.add_peer("peer_2", handler2)
        
        assert len(room.peers) == 2
        assert room.peers["peer_1"] == handler1
        assert room.peers["peer_2"] == handler2
    
    def test_remove_peer_existing(self):
        """Test removing an existing peer."""
        room = P2PRoom("test_room", "creator")
        mock_handler = MagicMock()
        room.add_peer("peer_1", mock_handler)
        
        room.remove_peer("peer_1")
        
        assert "peer_1" not in room.peers
    
    def test_remove_peer_nonexistent(self):
        """Test removing a non-existent peer (should not raise)."""
        room = P2PRoom("test_room", "creator")
        
        # Should not raise any exception
        room.remove_peer("nonexistent_peer")
        
        assert room.peers == {}
    
    def test_get_other_peer_exists(self):
        """Test getting the other peer in the room."""
        room = P2PRoom("test_room", "creator")
        handler1 = MagicMock()
        handler2 = MagicMock()
        room.add_peer("peer_1", handler1)
        room.add_peer("peer_2", handler2)
        
        other = room.get_other_peer("peer_1")
        
        assert other == handler2
    
    def test_get_other_peer_alone(self):
        """Test getting other peer when alone in room."""
        room = P2PRoom("test_room", "creator")
        handler1 = MagicMock()
        room.add_peer("peer_1", handler1)
        
        other = room.get_other_peer("peer_1")
        
        assert other is None
    
    def test_get_other_peer_empty_room(self):
        """Test getting other peer in empty room."""
        room = P2PRoom("test_room", "creator")
        
        other = room.get_other_peer("peer_1")
        
        assert other is None
    
    def test_broadcast_to_all_except_sender(self):
        """Test broadcasting message to all peers except sender."""
        room = P2PRoom("test_room", "creator")
        handler1 = MagicMock()
        handler2 = MagicMock()
        handler3 = MagicMock()
        room.add_peer("peer_1", handler1)
        room.add_peer("peer_2", handler2)
        room.add_peer("peer_3", handler3)
        
        message = {"type": "test", "data": "hello"}
        room.broadcast(message, exclude_peer="peer_1")
        
        handler1.write_message.assert_not_called()
        handler2.write_message.assert_called_once()
        handler3.write_message.assert_called_once()
        
        # Verify message content
        sent_message = json.loads(handler2.write_message.call_args[0][0])
        assert sent_message == message
    
    def test_broadcast_to_all(self):
        """Test broadcasting message to all peers (no exclusion)."""
        room = P2PRoom("test_room", "creator")
        handler1 = MagicMock()
        handler2 = MagicMock()
        room.add_peer("peer_1", handler1)
        room.add_peer("peer_2", handler2)
        
        message = {"type": "test"}
        room.broadcast(message)
        
        handler1.write_message.assert_called_once()
        handler2.write_message.assert_called_once()
    
    def test_broadcast_handles_exception(self):
        """Test broadcast handles exception from a peer gracefully."""
        room = P2PRoom("test_room", "creator")
        handler1 = MagicMock()
        handler1.write_message.side_effect = Exception("Connection lost")
        handler2 = MagicMock()
        room.add_peer("peer_1", handler1)
        room.add_peer("peer_2", handler2)
        
        # Should not raise exception
        room.broadcast({"type": "test"})
        
        # Second handler should still receive the message
        handler2.write_message.assert_called_once()
    
    def test_file_info_assignment(self):
        """Test assigning file info to room."""
        room = P2PRoom("test_room", "creator")
        file_info = {"name": "test.txt", "size": 1024, "type": "text/plain"}
        
        room.file_info = file_info
        
        assert room.file_info == file_info


# =============================================================================
# P2PRoomManager Tests
# =============================================================================

class TestP2PRoomManager:
    """Test cases for P2PRoomManager class."""
    
    def setup_method(self):
        """Set up a fresh room manager for each test."""
        self.manager = P2PRoomManager()
    
    def test_initialization(self):
        """Test room manager initialization."""
        assert self.manager.rooms == {}
        assert self.manager._cleanup_interval == 3600
        assert self.manager._max_room_age == 86400
    
    def test_create_room_basic(self):
        """Test basic room creation."""
        room = self.manager.create_room("creator_123")
        
        assert room is not None
        assert room.creator_id == "creator_123"
        assert room.room_id in self.manager.rooms
        assert room.allow_anonymous is False
    
    def test_create_room_with_anonymous(self):
        """Test room creation with anonymous access."""
        room = self.manager.create_room("creator_123", allow_anonymous=True)
        
        assert room.allow_anonymous is True
    
    def test_create_room_unique_ids(self):
        """Test that created rooms have unique IDs."""
        rooms = [self.manager.create_room(f"creator_{i}") for i in range(10)]
        room_ids = [r.room_id for r in rooms]
        
        assert len(set(room_ids)) == 10  # All unique
    
    def test_get_room_exists(self):
        """Test getting an existing room."""
        created_room = self.manager.create_room("creator")
        
        retrieved_room = self.manager.get_room(created_room.room_id)
        
        assert retrieved_room == created_room
    
    def test_get_room_not_exists(self):
        """Test getting a non-existent room."""
        result = self.manager.get_room("nonexistent_room_id")
        
        assert result is None
    
    def test_remove_room_exists(self):
        """Test removing an existing room."""
        room = self.manager.create_room("creator")
        room_id = room.room_id
        
        self.manager.remove_room(room_id)
        
        assert room_id not in self.manager.rooms
    
    def test_remove_room_not_exists(self):
        """Test removing a non-existent room (should not raise)."""
        # Should not raise any exception
        self.manager.remove_room("nonexistent_room_id")
    
    def test_cleanup_old_rooms(self):
        """Test cleanup of old rooms."""
        # Create a room and artificially age it
        room = self.manager.create_room("creator")
        room.created_at = time.time() - 100000  # Well past max age
        
        self.manager.cleanup_old_rooms()
        
        assert room.room_id not in self.manager.rooms
    
    def test_cleanup_keeps_new_rooms(self):
        """Test cleanup keeps recently created rooms."""
        room = self.manager.create_room("creator")
        
        self.manager.cleanup_old_rooms()
        
        assert room.room_id in self.manager.rooms
    
    def test_cleanup_mixed_rooms(self):
        """Test cleanup with mix of old and new rooms."""
        old_room = self.manager.create_room("old_creator")
        old_room.created_at = time.time() - 100000
        
        new_room = self.manager.create_room("new_creator")
        
        self.manager.cleanup_old_rooms()
        
        assert old_room.room_id not in self.manager.rooms
        assert new_room.room_id in self.manager.rooms


# =============================================================================
# P2PTransferHandler Tests
# =============================================================================

class TestP2PTransferHandler:
    """Test cases for P2PTransferHandler class."""
    
    def setup_method(self):
        """Set up test fixtures."""
        self.mock_app = MagicMock()
        self.mock_request = MagicMock()
        self.mock_app.settings = {'cookie_secret': 'test_secret'}
        self.mock_app.ui_modules = {}
        self.mock_app.ui_methods = {}
        # Clear room manager for each test
        room_manager.rooms.clear()
    
    def _create_handler(self):
        """Create a handler instance for testing."""
        handler = P2PTransferHandler(self.mock_app, self.mock_request)
        return prepare_handler(handler)
    
    def test_get_authenticated_user_no_room(self):
        """Test GET request with authenticated user and no room."""
        handler = self._create_handler()
        authenticate(handler, role='user', username='testuser')
        handler.get_argument = MagicMock(return_value=None)
        
        handler.get()
        
        handler.render.assert_called_once()
        call_args = handler.render.call_args
        assert call_args[0][0] == "p2p_transfer.html"
        assert call_args[1]["is_anonymous"] is False
        assert call_args[1]["room_id"] is None
    
    def test_get_authenticated_user_with_room(self):
        """Test GET request with authenticated user and room ID."""
        handler = self._create_handler()
        authenticate(handler, role='user', username='testuser')
        handler.get_argument = MagicMock(return_value="test_room_123")
        
        handler.get()
        
        handler.render.assert_called_once()
        call_args = handler.render.call_args
        assert call_args[1]["room_id"] == "test_room_123"
        assert call_args[1]["is_anonymous"] is False
    
    def test_get_unauthenticated_no_room_redirects(self):
        """Test GET request without auth and no room redirects to login."""
        handler = self._create_handler()
        handler.get_current_user = MagicMock(return_value=None)
        handler.get_argument = MagicMock(return_value=None)
        handler.get_login_url = MagicMock(return_value="/login")
        
        handler.get()
        
        handler.redirect.assert_called_once_with("/login")
        handler.render.assert_not_called()
    
    def test_get_unauthenticated_with_anonymous_room(self):
        """Test GET request with anonymous room allows access."""
        # Create an anonymous room first
        room = room_manager.create_room("creator", allow_anonymous=True)
        
        handler = self._create_handler()
        handler.get_current_user = MagicMock(return_value=None)
        handler.get_argument = MagicMock(return_value=room.room_id)
        
        handler.get()
        
        handler.render.assert_called_once()
        call_args = handler.render.call_args
        assert call_args[1]["is_anonymous"] is True
        assert call_args[1]["room_id"] == room.room_id
    
    def test_get_unauthenticated_with_non_anonymous_room(self):
        """Test GET with non-anonymous room redirects to login."""
        # Create a non-anonymous room
        room = room_manager.create_room("creator", allow_anonymous=False)
        
        handler = self._create_handler()
        handler.get_current_user = MagicMock(return_value=None)
        handler.get_argument = MagicMock(return_value=room.room_id)
        handler.get_login_url = MagicMock(return_value="/login")
        
        handler.get()
        
        handler.redirect.assert_called_once_with("/login")
    
    def test_get_unauthenticated_with_nonexistent_room(self):
        """Test GET with non-existent room redirects to login."""
        handler = self._create_handler()
        handler.get_current_user = MagicMock(return_value=None)
        handler.get_argument = MagicMock(return_value="nonexistent_room")
        handler.get_login_url = MagicMock(return_value="/login")
        
        handler.get()
        
        handler.redirect.assert_called_once_with("/login")


# =============================================================================
# P2PSignalingHandler Tests
# =============================================================================

class TestP2PSignalingHandler:
    """Test cases for P2PSignalingHandler WebSocket handler."""
    
    def setup_method(self):
        """Set up test fixtures."""
        self.mock_app = MagicMock()
        self.mock_request = MagicMock()
        self.mock_app.settings = {'cookie_secret': 'test_secret'}
        # Clear room manager for each test
        room_manager.rooms.clear()
    
    def _create_handler(self):
        """Create a handler instance for testing."""
        handler = P2PSignalingHandler(self.mock_app, self.mock_request)
        handler.write_message = MagicMock()
        handler.close = MagicMock()
        handler.get_argument = MagicMock(return_value=None)
        handler.get_secure_cookie = MagicMock(return_value=None)
        handler.initialize()
        return handler
    
    # -------------------------------------------------------------------------
    # initialize() tests
    # -------------------------------------------------------------------------
    
    def test_initialize(self):
        """Test handler initialization."""
        handler = self._create_handler()
        
        assert handler.peer_id is None
        assert handler.room is None
        assert handler.username is None
        assert handler.is_anonymous is False
        assert handler.pending_room_id is None
    
    # -------------------------------------------------------------------------
    # get_current_user() tests
    # -------------------------------------------------------------------------
    
    def test_get_current_user_no_cookie(self):
        """Test get_current_user with no cookie."""
        handler = self._create_handler()
        handler.get_secure_cookie = MagicMock(return_value=None)
        
        result = handler.get_current_user()
        
        assert result is None
    
    def test_get_current_user_json_dict_cookie(self):
        """Test get_current_user with JSON dict cookie."""
        handler = self._create_handler()
        user_data = {"username": "testuser", "role": "admin"}
        handler.get_secure_cookie = MagicMock(
            return_value=json.dumps(user_data).encode('utf-8')
        )
        
        result = handler.get_current_user()
        
        assert result == user_data
    
    def test_get_current_user_json_string_cookie(self):
        """Test get_current_user with JSON string cookie."""
        handler = self._create_handler()
        handler.get_secure_cookie = MagicMock(
            return_value=json.dumps("testuser").encode('utf-8')
        )
        
        result = handler.get_current_user()
        
        assert result == {"username": "testuser", "role": "user"}
    
    def test_get_current_user_plain_string_cookie(self):
        """Test get_current_user with plain string cookie."""
        handler = self._create_handler()
        handler.get_secure_cookie = MagicMock(return_value=b"plainuser")
        
        result = handler.get_current_user()
        
        assert result == {"username": "plainuser", "role": "user"}
    
    def test_get_current_user_invalid_cookie(self):
        """Test get_current_user with cookie that causes parsing error."""
        handler = self._create_handler()
        # Create a mock cookie that raises exception during decode
        mock_cookie = MagicMock()
        mock_cookie.decode = MagicMock(side_effect=Exception("Decode error"))
        handler.get_secure_cookie = MagicMock(return_value=mock_cookie)

        result = handler.get_current_user()

        assert result is None
    
    # -------------------------------------------------------------------------
    # open() tests
    # -------------------------------------------------------------------------
    
    def test_open_authenticated_user(self):
        """Test WebSocket open with authenticated user."""
        handler = self._create_handler()
        handler.get_secure_cookie = MagicMock(
            return_value=json.dumps({"username": "testuser", "role": "user"}).encode()
        )
        
        handler.open()
        
        assert handler.username == "testuser"
        assert handler.peer_id is not None
        assert handler.is_anonymous is False
        
        # Verify connected message sent
        handler.write_message.assert_called_once()
        message = json.loads(handler.write_message.call_args[0][0])
        assert message["type"] == "connected"
        assert message["username"] == "testuser"
        assert message["is_anonymous"] is False
    
    def test_open_unauthenticated_no_room(self):
        """Test WebSocket open without auth and no room."""
        handler = self._create_handler()
        handler.get_secure_cookie = MagicMock(return_value=None)
        handler.get_argument = MagicMock(return_value=None)
        
        handler.open()
        
        # Should send error and close
        handler.write_message.assert_called_once()
        message = json.loads(handler.write_message.call_args[0][0])
        assert message["type"] == "error"
        assert "Authentication" in message["message"]
        handler.close.assert_called_once_with(code=1008, reason="Authentication required")
    
    def test_open_anonymous_with_valid_room(self):
        """Test WebSocket open as anonymous with valid anonymous room."""
        # Create anonymous room first
        room = room_manager.create_room("creator", allow_anonymous=True)
        
        handler = self._create_handler()
        handler.get_secure_cookie = MagicMock(return_value=None)
        handler.get_argument = MagicMock(return_value=room.room_id)
        
        handler.open()
        
        assert handler.is_anonymous is True
        assert handler.pending_room_id == room.room_id
        assert handler.username.startswith("Guest_")
        assert handler.peer_id is not None
        
        # Verify connected message
        message = json.loads(handler.write_message.call_args[0][0])
        assert message["type"] == "connected"
        assert message["is_anonymous"] is True
        assert message["pending_room"] == room.room_id
    
    def test_open_anonymous_with_non_anonymous_room(self):
        """Test WebSocket open as anonymous with non-anonymous room."""
        room = room_manager.create_room("creator", allow_anonymous=False)
        
        handler = self._create_handler()
        handler.get_secure_cookie = MagicMock(return_value=None)
        handler.get_argument = MagicMock(return_value=room.room_id)
        
        handler.open()
        
        # Should reject
        message = json.loads(handler.write_message.call_args[0][0])
        assert message["type"] == "error"
        handler.close.assert_called_once()
    
    def test_open_anonymous_with_nonexistent_room(self):
        """Test WebSocket open as anonymous with non-existent room."""
        handler = self._create_handler()
        handler.get_secure_cookie = MagicMock(return_value=None)
        handler.get_argument = MagicMock(return_value="nonexistent_room")
        
        handler.open()
        
        # Should reject
        message = json.loads(handler.write_message.call_args[0][0])
        assert message["type"] == "error"
        handler.close.assert_called_once()
    
    # -------------------------------------------------------------------------
    # on_message() tests
    # -------------------------------------------------------------------------
    
    def test_on_message_create_room(self):
        """Test on_message with create_room type."""
        handler = self._create_handler()
        handler.peer_id = "test_peer"
        handler.username = "testuser"
        handler.is_anonymous = False
        
        message = json.dumps({"type": "create_room"})
        handler.on_message(message)
        
        # Should create room and send response
        assert handler.room is not None
        response = json.loads(handler.write_message.call_args[0][0])
        assert response["type"] == "room_created"
    
    def test_on_message_invalid_json(self):
        """Test on_message with invalid JSON."""
        handler = self._create_handler()
        handler.peer_id = "test_peer"
        
        handler.on_message("not valid json {{{")
        
        response = json.loads(handler.write_message.call_args[0][0])
        assert response["type"] == "error"
        assert "Invalid JSON" in response["message"]
    
    def test_on_message_unknown_type(self):
        """Test on_message with unknown message type."""
        handler = self._create_handler()
        handler.peer_id = "test_peer"
        
        message = json.dumps({"type": "unknown_type"})
        handler.on_message(message)
        
        # Should not crash, just log warning
        # No response expected for unknown type
    
    def test_on_message_exception_handling(self):
        """Test on_message handles exceptions gracefully."""
        handler = self._create_handler()
        handler.peer_id = "test_peer"
        handler._handle_create_room = MagicMock(side_effect=Exception("Test error"))
        
        message = json.dumps({"type": "create_room"})
        handler.on_message(message)
        
        response = json.loads(handler.write_message.call_args[0][0])
        assert response["type"] == "error"
    
    # -------------------------------------------------------------------------
    # _handle_create_room() tests
    # -------------------------------------------------------------------------
    
    def test_handle_create_room_authenticated(self):
        """Test create_room for authenticated user."""
        handler = self._create_handler()
        handler.peer_id = "test_peer"
        handler.username = "testuser"
        handler.is_anonymous = False
        
        handler._handle_create_room({})
        
        assert handler.room is not None
        assert handler.room.creator_id == "test_peer"
        response = json.loads(handler.write_message.call_args[0][0])
        assert response["type"] == "room_created"
        assert "room_id" in response
    
    def test_handle_create_room_anonymous_denied(self):
        """Test create_room denied for anonymous user."""
        handler = self._create_handler()
        handler.peer_id = "test_peer"
        handler.username = "Guest_123"
        handler.is_anonymous = True
        
        handler._handle_create_room({})
        
        assert handler.room is None
        response = json.loads(handler.write_message.call_args[0][0])
        assert response["type"] == "error"
        assert "Anonymous" in response["message"]
    
    def test_handle_create_room_with_file_info(self):
        """Test create_room with file info."""
        handler = self._create_handler()
        handler.peer_id = "test_peer"
        handler.username = "testuser"
        handler.is_anonymous = False
        
        file_info = {"name": "test.txt", "size": 1024}
        handler._handle_create_room({"file_info": file_info})
        
        assert handler.room.file_info == file_info
        response = json.loads(handler.write_message.call_args[0][0])
        assert response["file_info"] == file_info
    
    def test_handle_create_room_allow_anonymous(self):
        """Test create_room with allow_anonymous flag."""
        handler = self._create_handler()
        handler.peer_id = "test_peer"
        handler.username = "testuser"
        handler.is_anonymous = False
        
        handler._handle_create_room({"allow_anonymous": True})
        
        assert handler.room.allow_anonymous is True
        response = json.loads(handler.write_message.call_args[0][0])
        assert response["allow_anonymous"] is True
    
    def test_handle_create_room_leaves_existing_room(self):
        """Test create_room leaves existing room first."""
        handler = self._create_handler()
        handler.peer_id = "test_peer"
        handler.username = "testuser"
        handler.is_anonymous = False
        
        # Create first room
        handler._handle_create_room({})
        first_room = handler.room
        
        # Create second room (should leave first)
        handler._handle_create_room({})
        
        assert handler.room != first_room
        assert "test_peer" not in first_room.peers
    
    # -------------------------------------------------------------------------
    # _handle_join_room() tests
    # -------------------------------------------------------------------------
    
    def test_handle_join_room_success(self):
        """Test successful room join."""
        # Create a room first
        room = room_manager.create_room("creator")
        
        handler = self._create_handler()
        handler.peer_id = "joiner_peer"
        handler.username = "joiner"
        handler.is_anonymous = False
        
        handler._handle_join_room({"room_id": room.room_id})
        
        assert handler.room == room
        assert "joiner_peer" in room.peers
        response = json.loads(handler.write_message.call_args[0][0])
        assert response["type"] == "room_joined"
    
    def test_handle_join_room_no_room_id(self):
        """Test join_room without room_id."""
        handler = self._create_handler()
        handler.peer_id = "test_peer"
        
        handler._handle_join_room({})
        
        response = json.loads(handler.write_message.call_args[0][0])
        assert response["type"] == "error"
        assert "Room ID required" in response["message"]
    
    def test_handle_join_room_not_found(self):
        """Test join_room with non-existent room."""
        handler = self._create_handler()
        handler.peer_id = "test_peer"
        
        handler._handle_join_room({"room_id": "nonexistent"})
        
        response = json.loads(handler.write_message.call_args[0][0])
        assert response["type"] == "error"
        assert "not found" in response["message"]
    
    def test_handle_join_room_full(self):
        """Test join_room when room is full."""
        room = room_manager.create_room("creator")
        room.add_peer("peer1", MagicMock())
        room.add_peer("peer2", MagicMock())
        
        handler = self._create_handler()
        handler.peer_id = "peer3"
        handler.username = "user3"
        
        handler._handle_join_room({"room_id": room.room_id})
        
        response = json.loads(handler.write_message.call_args[0][0])
        assert response["type"] == "error"
        assert "full" in response["message"]
    
    def test_handle_join_room_anonymous_allowed(self):
        """Test anonymous user joining anonymous-allowed room."""
        room = room_manager.create_room("creator", allow_anonymous=True)
        
        handler = self._create_handler()
        handler.peer_id = "anon_peer"
        handler.username = "Guest_123"
        handler.is_anonymous = True
        
        handler._handle_join_room({"room_id": room.room_id})
        
        response = json.loads(handler.write_message.call_args[0][0])
        assert response["type"] == "room_joined"
    
    def test_handle_join_room_anonymous_denied(self):
        """Test anonymous user denied from non-anonymous room."""
        room = room_manager.create_room("creator", allow_anonymous=False)
        
        handler = self._create_handler()
        handler.peer_id = "anon_peer"
        handler.username = "Guest_123"
        handler.is_anonymous = True
        
        handler._handle_join_room({"room_id": room.room_id})
        
        response = json.loads(handler.write_message.call_args[0][0])
        assert response["type"] == "error"
        assert "login" in response["message"].lower()
    
    def test_handle_join_room_notifies_existing_peer(self):
        """Test that joining notifies existing peer."""
        room = room_manager.create_room("creator")
        existing_handler = MagicMock()
        room.add_peer("existing_peer", existing_handler)
        
        handler = self._create_handler()
        handler.peer_id = "joiner_peer"
        handler.username = "joiner"
        handler.is_anonymous = False
        
        handler._handle_join_room({"room_id": room.room_id})
        
        # Existing peer should receive notification
        existing_handler.write_message.assert_called_once()
        notification = json.loads(existing_handler.write_message.call_args[0][0])
        assert notification["type"] == "peer_joined"
        assert notification["peer_id"] == "joiner_peer"
    
    def test_handle_join_room_with_file_info(self):
        """Test join_room receives file info."""
        room = room_manager.create_room("creator")
        room.file_info = {"name": "test.txt", "size": 1024}
        
        handler = self._create_handler()
        handler.peer_id = "joiner_peer"
        handler.username = "joiner"
        handler.is_anonymous = False
        
        handler._handle_join_room({"room_id": room.room_id})
        
        response = json.loads(handler.write_message.call_args[0][0])
        assert response["file_info"] == {"name": "test.txt", "size": 1024}
    
    # -------------------------------------------------------------------------
    # _handle_leave_room() tests
    # -------------------------------------------------------------------------
    
    def test_handle_leave_room_success(self):
        """Test successful room leave."""
        room = room_manager.create_room("creator")
        
        handler = self._create_handler()
        handler.peer_id = "test_peer"
        handler.username = "testuser"
        handler.room = room
        room.add_peer("test_peer", handler)
        
        handler._handle_leave_room()
        
        assert handler.room is None
        assert "test_peer" not in room.peers
    
    def test_handle_leave_room_not_in_room(self):
        """Test leave_room when not in a room."""
        handler = self._create_handler()
        handler.peer_id = "test_peer"
        handler.room = None
        
        # Should not raise
        handler._handle_leave_room()
        
        assert handler.room is None
    
    def test_handle_leave_room_notifies_peers(self):
        """Test leave_room notifies other peers."""
        room = room_manager.create_room("creator")
        other_handler = MagicMock()
        room.add_peer("other_peer", other_handler)
        
        handler = self._create_handler()
        handler.peer_id = "leaving_peer"
        handler.username = "leaver"
        handler.room = room
        room.add_peer("leaving_peer", handler)
        
        handler._handle_leave_room()
        
        other_handler.write_message.assert_called_once()
        notification = json.loads(other_handler.write_message.call_args[0][0])
        assert notification["type"] == "peer_left"
        assert notification["peer_id"] == "leaving_peer"
    
    def test_handle_leave_room_removes_empty_room(self):
        """Test leave_room removes empty room from manager."""
        room = room_manager.create_room("creator")
        room_id = room.room_id
        
        handler = self._create_handler()
        handler.peer_id = "only_peer"
        handler.username = "user"
        handler.room = room
        room.add_peer("only_peer", handler)
        
        handler._handle_leave_room()
        
        assert room_id not in room_manager.rooms
    
    # -------------------------------------------------------------------------
    # _handle_offer() tests
    # -------------------------------------------------------------------------
    
    def test_handle_offer_success(self):
        """Test offer forwarding to other peer."""
        room = room_manager.create_room("creator")
        other_handler = MagicMock()
        room.add_peer("other_peer", other_handler)
        
        handler = self._create_handler()
        handler.peer_id = "sender_peer"
        handler.room = room
        room.add_peer("sender_peer", handler)
        
        sdp_data = {"type": "offer", "sdp": "test_sdp_content"}
        handler._handle_offer({"sdp": sdp_data})
        
        other_handler.write_message.assert_called_once()
        message = json.loads(other_handler.write_message.call_args[0][0])
        assert message["type"] == "offer"
        assert message["sdp"] == sdp_data
        assert message["from_peer"] == "sender_peer"
    
    def test_handle_offer_no_room(self):
        """Test offer when not in room."""
        handler = self._create_handler()
        handler.peer_id = "test_peer"
        handler.room = None
        
        # Should not crash
        handler._handle_offer({"sdp": "test"})
    
    def test_handle_offer_no_other_peer(self):
        """Test offer when alone in room."""
        room = room_manager.create_room("creator")
        
        handler = self._create_handler()
        handler.peer_id = "only_peer"
        handler.room = room
        room.add_peer("only_peer", handler)
        
        # Should not crash
        handler._handle_offer({"sdp": "test"})
    
    # -------------------------------------------------------------------------
    # _handle_answer() tests
    # -------------------------------------------------------------------------
    
    def test_handle_answer_success(self):
        """Test answer forwarding to other peer."""
        room = room_manager.create_room("creator")
        other_handler = MagicMock()
        room.add_peer("other_peer", other_handler)
        
        handler = self._create_handler()
        handler.peer_id = "sender_peer"
        handler.room = room
        room.add_peer("sender_peer", handler)
        
        sdp_data = {"type": "answer", "sdp": "test_answer_sdp"}
        handler._handle_answer({"sdp": sdp_data})
        
        other_handler.write_message.assert_called_once()
        message = json.loads(other_handler.write_message.call_args[0][0])
        assert message["type"] == "answer"
        assert message["sdp"] == sdp_data
        assert message["from_peer"] == "sender_peer"
    
    def test_handle_answer_no_room(self):
        """Test answer when not in room."""
        handler = self._create_handler()
        handler.peer_id = "test_peer"
        handler.room = None
        
        # Should not crash
        handler._handle_answer({"sdp": "test"})
    
    # -------------------------------------------------------------------------
    # _handle_ice_candidate() tests
    # -------------------------------------------------------------------------
    
    def test_handle_ice_candidate_success(self):
        """Test ICE candidate forwarding to other peer."""
        room = room_manager.create_room("creator")
        other_handler = MagicMock()
        room.add_peer("other_peer", other_handler)
        
        handler = self._create_handler()
        handler.peer_id = "sender_peer"
        handler.room = room
        room.add_peer("sender_peer", handler)
        
        candidate_data = {"candidate": "test_ice_candidate", "sdpMid": "0"}
        handler._handle_ice_candidate({"candidate": candidate_data})
        
        other_handler.write_message.assert_called_once()
        message = json.loads(other_handler.write_message.call_args[0][0])
        assert message["type"] == "ice_candidate"
        assert message["candidate"] == candidate_data
        assert message["from_peer"] == "sender_peer"
    
    def test_handle_ice_candidate_no_room(self):
        """Test ICE candidate when not in room."""
        handler = self._create_handler()
        handler.peer_id = "test_peer"
        handler.room = None
        
        # Should not crash
        handler._handle_ice_candidate({"candidate": "test"})
    
    # -------------------------------------------------------------------------
    # _handle_file_info() tests
    # -------------------------------------------------------------------------
    
    def test_handle_file_info_success(self):
        """Test file info update and broadcast."""
        room = room_manager.create_room("creator")
        other_handler = MagicMock()
        room.add_peer("other_peer", other_handler)
        
        handler = self._create_handler()
        handler.peer_id = "sender_peer"
        handler.room = room
        room.add_peer("sender_peer", handler)
        
        file_info = {"name": "test.pdf", "size": 2048, "type": "application/pdf"}
        handler._handle_file_info({"file_info": file_info})
        
        assert room.file_info == file_info
        other_handler.write_message.assert_called_once()
        message = json.loads(other_handler.write_message.call_args[0][0])
        assert message["type"] == "file_info_updated"
        assert message["file_info"] == file_info
    
    def test_handle_file_info_no_room(self):
        """Test file info when not in room."""
        handler = self._create_handler()
        handler.peer_id = "test_peer"
        handler.room = None
        
        # Should not crash
        handler._handle_file_info({"file_info": {"name": "test.txt"}})
    
    # -------------------------------------------------------------------------
    # on_close() tests
    # -------------------------------------------------------------------------
    
    def test_on_close_in_room(self):
        """Test WebSocket close while in room."""
        room = room_manager.create_room("creator")
        room_id = room.room_id
        
        handler = self._create_handler()
        handler.peer_id = "test_peer"
        handler.username = "testuser"
        handler.room = room
        room.add_peer("test_peer", handler)
        
        handler.on_close()
        
        assert handler.room is None
        assert "test_peer" not in room.peers
    
    def test_on_close_not_in_room(self):
        """Test WebSocket close when not in room."""
        handler = self._create_handler()
        handler.peer_id = "test_peer"
        handler.room = None
        
        # Should not crash
        handler.on_close()
    
    # -------------------------------------------------------------------------
    # check_origin() tests
    # -------------------------------------------------------------------------
    
    def test_check_origin(self):
        """Test origin check delegates to utility function."""
        handler = self._create_handler()
        
        with patch('aird.handlers.p2p_handlers.is_valid_websocket_origin', return_value=True) as mock_check:
            result = handler.check_origin("http://localhost:8000")
            
            mock_check.assert_called_once_with(handler, "http://localhost:8000")
            assert result is True
    
    def test_check_origin_invalid(self):
        """Test origin check returns false for invalid origin."""
        handler = self._create_handler()
        
        with patch('aird.handlers.p2p_handlers.is_valid_websocket_origin', return_value=False):
            result = handler.check_origin("http://evil.com")
            
            assert result is False


# =============================================================================
# Integration-style Tests
# =============================================================================

class TestP2PIntegration:
    """Integration-style tests for P2P flow."""
    
    def setup_method(self):
        """Clear room manager for each test."""
        room_manager.rooms.clear()
    
    def test_full_signaling_flow(self):
        """Test complete signaling flow between two peers."""
        mock_app = MagicMock()
        mock_app.settings = {'cookie_secret': 'test_secret'}
        
        # Create sender handler
        sender = P2PSignalingHandler(mock_app, MagicMock())
        sender.write_message = MagicMock()
        sender.close = MagicMock()
        sender.get_argument = MagicMock(return_value=None)
        sender.get_secure_cookie = MagicMock(
            return_value=json.dumps({"username": "sender", "role": "user"}).encode()
        )
        sender.initialize()
        sender.open()
        
        # Create receiver handler
        receiver = P2PSignalingHandler(mock_app, MagicMock())
        receiver.write_message = MagicMock()
        receiver.close = MagicMock()
        receiver.get_argument = MagicMock(return_value=None)
        receiver.get_secure_cookie = MagicMock(
            return_value=json.dumps({"username": "receiver", "role": "user"}).encode()
        )
        receiver.initialize()
        receiver.open()
        
        # Sender creates room
        sender.on_message(json.dumps({
            "type": "create_room",
            "file_info": {"name": "test.txt", "size": 100}
        }))
        
        room_created_msg = json.loads(sender.write_message.call_args[0][0])
        assert room_created_msg["type"] == "room_created"
        room_id = room_created_msg["room_id"]
        
        # Receiver joins room
        receiver.on_message(json.dumps({
            "type": "join_room",
            "room_id": room_id
        }))
        
        room_joined_msg = json.loads(receiver.write_message.call_args[0][0])
        assert room_joined_msg["type"] == "room_joined"
        assert room_joined_msg["file_info"]["name"] == "test.txt"
        
        # Verify sender was notified of peer join
        sender.write_message.reset_mock()
        # The notification would have been sent via broadcast
        
        # Sender sends offer
        sender.on_message(json.dumps({
            "type": "offer",
            "sdp": {"type": "offer", "sdp": "v=0..."}
        }))
        
        # Verify receiver got offer
        offer_msg = json.loads(receiver.write_message.call_args[0][0])
        assert offer_msg["type"] == "offer"
        
        # Receiver sends answer
        receiver.on_message(json.dumps({
            "type": "answer",
            "sdp": {"type": "answer", "sdp": "v=0..."}
        }))
        
        # Verify sender got answer
        answer_msg = json.loads(sender.write_message.call_args[0][0])
        assert answer_msg["type"] == "answer"
        
        # Exchange ICE candidates
        sender.on_message(json.dumps({
            "type": "ice_candidate",
            "candidate": {"candidate": "candidate:1 ..."}
        }))
        
        ice_msg = json.loads(receiver.write_message.call_args[0][0])
        assert ice_msg["type"] == "ice_candidate"
    
    def test_anonymous_sharing_flow(self):
        """Test anonymous file sharing flow."""
        mock_app = MagicMock()
        mock_app.settings = {'cookie_secret': 'test_secret'}
        
        # Authenticated sender creates anonymous room
        sender = P2PSignalingHandler(mock_app, MagicMock())
        sender.write_message = MagicMock()
        sender.close = MagicMock()
        sender.get_argument = MagicMock(return_value=None)
        sender.get_secure_cookie = MagicMock(
            return_value=json.dumps({"username": "sender", "role": "user"}).encode()
        )
        sender.initialize()
        sender.open()
        
        # Create room with anonymous access
        sender.on_message(json.dumps({
            "type": "create_room",
            "allow_anonymous": True,
            "file_info": {"name": "shared.pdf", "size": 5000}
        }))
        
        room_msg = json.loads(sender.write_message.call_args[0][0])
        room_id = room_msg["room_id"]
        assert room_msg["allow_anonymous"] is True
        
        # Anonymous receiver connects
        receiver = P2PSignalingHandler(mock_app, MagicMock())
        receiver.write_message = MagicMock()
        receiver.close = MagicMock()
        receiver.get_argument = MagicMock(return_value=room_id)
        receiver.get_secure_cookie = MagicMock(return_value=None)  # Not logged in
        receiver.initialize()
        receiver.open()
        
        # Verify anonymous connection was successful
        connect_msg = json.loads(receiver.write_message.call_args[0][0])
        assert connect_msg["type"] == "connected"
        assert connect_msg["is_anonymous"] is True
        
        # Anonymous receiver joins room
        receiver.on_message(json.dumps({
            "type": "join_room",
            "room_id": room_id
        }))
        
        join_msg = json.loads(receiver.write_message.call_args[0][0])
        assert join_msg["type"] == "room_joined"
        assert join_msg["file_info"]["name"] == "shared.pdf"
