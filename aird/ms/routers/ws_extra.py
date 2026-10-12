"""Extra Starlette WebSocket routes (bulk, P2P signaling, policy decisions)."""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from starlette.routing import WebSocketRoute
from starlette.websockets import WebSocket, WebSocketDisconnect

from aird.core.bulk.ws_bridge import WsBulkSession
from aird.core.events import PolicyDecisionEvent, TransferStartedEvent, now_ts
from aird.handlers.abac_handlers import PolicyDecisionsWebSocket
from aird.handlers.bulk_handlers import BulkWebSocketHandler
from aird.handlers.constants import AUTH_REQUIRED
from aird.handlers.p2p_handlers import P2PRoom, P2PRoomManager, room_manager
from aird.ms.ctx import current_user, get_state
from aird.ms.deps import is_admin_user
from aird.ms.ws_support import (
    StarletteWsBridge,
    keepalive_receive_loop,
    register_managed_connection,
    reject_oversized_ws_message,
    request_from_websocket,
    require_ws_user,
    send_json,
    ws_origin_ok,
)
from aird.services.p2p_service import P2PSignalingService
from aird.utils.util import is_feature_enabled

logger = logging.getLogger(__name__)

_BULK_MANAGER = BulkWebSocketHandler.connection_manager
_POLICY_MANAGER = PolicyDecisionsWebSocket.connection_manager


def _client_ip(request) -> str:
    if request.client:
        return request.client.host
    return ""


def _bulk_engine(settings: dict[str, Any]):
    if settings.get("app_context") is None:
        raise RuntimeError("Server misconfigured")
    engine = settings.get("bulk_engine")
    if engine is None:
        raise RuntimeError("Bulk transfer not enabled")
    return engine


async def _bulk_send_status(websocket: WebSocket, status: int) -> None:
    try:
        await websocket.send_bytes(bytes([status]))
    except Exception:
        pass
    finally:
        try:
            await websocket.close()
        except Exception:
            pass


async def bulk_websocket(websocket: WebSocket) -> None:
    """Browser bulk upload: binary frames → BulkEngine (PUT only)."""
    request = request_from_websocket(websocket)
    if not ws_origin_ok(websocket, request):
        await websocket.close(code=1008, reason="Invalid origin")
        return

    await websocket.accept()
    bridge = StarletteWsBridge(websocket)

    user = await require_ws_user(
        websocket,
        request,
        close_reason=AUTH_REQUIRED,
    )
    if not user:
        return

    if not await register_managed_connection(websocket, bridge, _BULK_MANAGER):
        return

    if not is_feature_enabled("file_upload", True):
        await websocket.close(code=4403, reason="Upload disabled")
        _BULK_MANAGER.remove_connection(bridge)
        return

    settings = get_state(request).settings
    peer = _client_ip(request)
    session: WsBulkSession | None = None
    loop = asyncio.get_running_loop()

    try:
        while True:
            message = await websocket.receive()
            if message["type"] == "websocket.disconnect":
                break
            if message["type"] != "websocket.receive":
                continue

            if message.get("text") is not None:
                await websocket.close(code=4400, reason="Binary frames required")
                break

            data = message.get("bytes")
            if not data:
                continue

            if session is None:
                try:
                    engine = _bulk_engine(settings)
                except RuntimeError:
                    await websocket.close(code=1013, reason="Bulk transfer not enabled")
                    break

                def on_complete(status: int) -> None:
                    asyncio.run_coroutine_threadsafe(
                        _bulk_send_status(websocket, status), loop
                    )

                session = WsBulkSession(engine, on_complete)
                session.feed(data)
                session.start(peer)
            else:
                session.feed(data)
    except WebSocketDisconnect:
        pass
    finally:
        if session and session.started:
            session.finish_feed()
        _BULK_MANAGER.remove_connection(bridge)


class _P2PSignalingPeer:
    """Tornado-compatible peer surface for P2P room_manager."""

    def __init__(
        self,
        bridge: StarletteWsBridge,
        *,
        settings: dict[str, Any],
        room_id_arg: str | None,
        user: dict[str, Any] | None,
    ) -> None:
        self._bridge = bridge
        self.peer_id: str | None = None
        self.room: P2PRoom | None = None
        self.username: str | None = None
        self.is_anonymous = False
        self.pending_room_id: str | None = None
        self._room_manager: P2PRoomManager = settings.get("room_manager", room_manager)
        self._event_bus = settings.get("event_bus")
        if self._event_bus is None:
            app_ctx = settings.get("app_context")
            if app_ctx is not None:
                self._event_bus = app_ctx.event_bus
        services = settings.get("services", {})
        self._service: P2PSignalingService = services.get(
            "p2p_signaling_service"
        ) or P2PSignalingService(self._room_manager)
        self._room_id_arg = room_id_arg
        self._user = user

    def write_message(self, message: str | bytes) -> None:
        self._bridge.write_message(message)

    async def open(self) -> bool:
        if not is_feature_enabled("p2p_transfer", True):
            await send_json(
                self._bridge.websocket,
                {
                    "type": "error",
                    "message": "P2P Transfer is currently disabled by administrator",
                },
            )
            await self._bridge.websocket.close(code=1008, reason="Feature disabled")
            return False

        room_id = self._room_id_arg
        user = self._user
        if not user:
            self.is_anonymous = True
            if room_id:
                room = self._room_manager.get_room(room_id)
                if room and room.allow_anonymous:
                    self.pending_room_id = room_id
                elif room:
                    await send_json(
                        self._bridge.websocket,
                        {
                            "type": "error",
                            "message": (
                                "This share link requires an Aird login. "
                                "Sign in, then open the link again."
                            ),
                            "code": "requires_login",
                        },
                    )
                    await self._bridge.websocket.close(
                        code=1008, reason="Authentication required for room"
                    )
                    return False
                else:
                    await send_json(
                        self._bridge.websocket,
                        {
                            "type": "error",
                            "message": (
                                "Share room not found. It may have expired, "
                                "or the server was restarted. Ask the sender for a new link."
                            ),
                            "code": "room_not_found",
                        },
                    )
                    await self._bridge.websocket.close(code=1008, reason="Room not found")
                    return False

            self.username, self.peer_id = self._service.make_anonymous_identity()
            logger.info(
                "P2P WebSocket opened for anonymous user: %s, peer_id: %s",
                self.username,
                self.peer_id,
            )
            await send_json(
                self._bridge.websocket,
                self._service.connected_payload(
                    self.peer_id,
                    self.username,
                    is_anonymous=True,
                    pending_room=self.pending_room_id,
                ),
            )
            return True

        self.username = user.get("username", "anonymous")
        self.peer_id = self._service.make_user_peer_id()
        logger.info(
            "P2P WebSocket opened for user: %s, peer_id: %s",
            self.username,
            self.peer_id,
        )
        await send_json(
            self._bridge.websocket,
            self._service.connected_payload(
                self.peer_id, self.username, is_anonymous=False
            ),
        )
        return True

    async def on_message(self, message: str) -> None:
        if reject_oversized_ws_message(message):
            await send_json(
                self._bridge.websocket,
                {"type": "error", "message": "Message too large"},
            )
            return
        try:
            data = json.loads(message)
            msg_type = data.get("type")
            logger.info(
                "P2P message received from %s: type=%s", self.peer_id, msg_type
            )
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
            result = handler(data)
            if asyncio.iscoroutine(result):
                await result
        except json.JSONDecodeError:
            logger.exception("Invalid JSON received")
            await send_json(
                self._bridge.websocket,
                {"type": "error", "message": "Invalid JSON"},
            )
        except Exception as exc:
            logger.exception("Error handling message")
            await send_json(
                self._bridge.websocket,
                {"type": "error", "message": str(exc)},
            )

    async def _handle_create_room(self, data: dict) -> None:
        logger.info("Creating room for peer %s, user %s", self.peer_id, self.username)
        if self.room:
            self._handle_leave_room()

        allow_anonymous = data.get("allow_anonymous", False)
        room = self._service.create_room(
            self.peer_id,
            allow_anonymous=allow_anonymous,
            file_info=data.get("file_info"),
        )
        room.add_peer(self.peer_id, self)
        self.room = room

        response = {
            "type": "room_created",
            "room_id": room.room_id,
            "file_info": room.file_info,
            "allow_anonymous": room.allow_anonymous,
        }
        await send_json(self._bridge.websocket, response)
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

    async def _handle_join_room(self, data: dict) -> None:
        room_id = data.get("room_id")
        if not room_id:
            await send_json(
                self._bridge.websocket,
                {"type": "error", "message": "Room ID required"},
            )
            return

        room = self._service.join_room(room_id)
        if not room:
            await send_json(
                self._bridge.websocket,
                {"type": "error", "message": "Room not found"},
            )
            return

        if self.is_anonymous and not room.allow_anonymous:
            await send_json(
                self._bridge.websocket,
                {
                    "type": "error",
                    "message": (
                        "This share link requires login. "
                        "Please log in to receive the file."
                    ),
                },
            )
            return

        if room.peer_count >= 2:
            await send_json(
                self._bridge.websocket,
                {"type": "error", "message": "Room is full"},
            )
            return

        if self.room:
            self._handle_leave_room()

        room.empty_since = None
        room.add_peer(self.peer_id, self)
        self.room = room

        await send_json(
            self._bridge.websocket,
            {
                "type": "room_joined",
                "room_id": room.room_id,
                "file_info": room.file_info,
                "peer_count": room.peer_count,
                "allow_anonymous": room.allow_anonymous,
            },
        )
        room.broadcast(
            {
                "type": "peer_joined",
                "peer_id": self.peer_id,
                "username": self.username,
                "is_anonymous": self.is_anonymous,
            },
            exclude_peer=self.peer_id,
        )

    def _handle_leave_room(self) -> None:
        if not self.room:
            return
        room_id = self.room.room_id
        self.room.remove_peer(self.peer_id)
        self.room.broadcast(
            {"type": "peer_left", "peer_id": self.peer_id, "username": self.username}
        )
        if self.room.is_empty():
            self._service.leave_room(self.room)
        self.room = None
        logger.info("User %s left room %s", self.username, room_id)

    def _handle_offer(self, data: dict) -> None:
        if not self.room:
            return
        if self.room.get_other_peer(self.peer_id):
            self._service.forward_to_other_peer(
                self.room,
                self.peer_id,
                {"type": "offer", "sdp": data.get("sdp"), "from_peer": self.peer_id},
            )

    def _handle_answer(self, data: dict) -> None:
        if not self.room:
            return
        if self.room.get_other_peer(self.peer_id):
            self._service.forward_to_other_peer(
                self.room,
                self.peer_id,
                {
                    "type": "answer",
                    "sdp": data.get("sdp"),
                    "from_peer": self.peer_id,
                },
            )

    def _handle_ice_candidate(self, data: dict) -> None:
        if not self.room:
            return
        if self.room.get_other_peer(self.peer_id):
            self._service.forward_to_other_peer(
                self.room,
                self.peer_id,
                {
                    "type": "ice_candidate",
                    "candidate": data.get("candidate"),
                    "from_peer": self.peer_id,
                },
            )

    def _handle_file_info(self, data: dict) -> None:
        if not self.room:
            return
        self.room.file_info = data.get("file_info")
        self.room.broadcast(
            {"type": "file_info_updated", "file_info": self.room.file_info},
            exclude_peer=self.peer_id,
        )

    def _handle_restart_connection(self) -> None:
        if not self.room:
            return
        if self.room.get_other_peer(self.peer_id):
            self._service.forward_to_other_peer(
                self.room,
                self.peer_id,
                {
                    "type": "restart_connection",
                    "from_peer": self.peer_id,
                    "username": self.username,
                },
            )

    def on_close(self) -> None:
        logger.info("P2P WebSocket closed for peer: %s", self.peer_id)
        self._handle_leave_room()


async def p2p_signal_websocket(websocket: WebSocket) -> None:
    request = request_from_websocket(websocket)
    if not ws_origin_ok(websocket, request):
        await websocket.close(code=1008, reason="Invalid origin")
        return

    await websocket.accept()
    bridge = StarletteWsBridge(websocket)
    settings = get_state(request).settings
    room_id = websocket.query_params.get("room")
    user = current_user(request)

    peer = _P2PSignalingPeer(
        bridge,
        settings=settings,
        room_id_arg=room_id,
        user=user if isinstance(user, dict) else None,
    )
    if not await peer.open():
        return

    async def _on_text(text: str) -> None:
        await peer.on_message(text)

    try:
        await keepalive_receive_loop(websocket, on_text=_on_text)
    finally:
        peer.on_close()


async def policy_decisions_websocket(websocket: WebSocket) -> None:
    """Stream PolicyDecisionEvent payloads to admin clients."""
    request = request_from_websocket(websocket)
    if not ws_origin_ok(websocket, request):
        await websocket.close(code=1008, reason="Invalid origin")
        return

    await websocket.accept()
    bridge = StarletteWsBridge(websocket)

    user = current_user(request)
    if not is_admin_user(user):
        await websocket.close(code=4401, reason="admin only")
        return

    if not await register_managed_connection(websocket, bridge, _POLICY_MANAGER):
        return

    settings = get_state(request).settings
    bus = settings.get("event_bus")
    if bus is None:
        app_ctx = settings.get("app_context")
        if app_ctx is not None:
            bus = app_ctx.event_bus

    def _on_decision(event: PolicyDecisionEvent) -> None:
        try:
            bridge.write_message(
                json.dumps(
                    {
                        "type": "policy_decision",
                        "username": event.username,
                        "action": event.action,
                        "resource": event.resource,
                        "decision": event.decision,
                        "reason": event.reason,
                        "policy_id": event.matched_policy_id,
                        "policy_name": event.matched_policy_name,
                        "ip": event.ip,
                        "decided_at": event.decided_at,
                    }
                )
            )
        except Exception:
            logger.debug("policy decision broadcast failed", exc_info=True)

    if bus is not None:
        bus.subscribe(PolicyDecisionEvent, _on_decision)

    try:
        await keepalive_receive_loop(websocket)
    finally:
        _POLICY_MANAGER.remove_connection(bridge)


routes = [
    WebSocketRoute("/ws/bulk", bulk_websocket),
    WebSocketRoute("/p2p/signal", p2p_signal_websocket),
    WebSocketRoute("/ws/policy-decisions", policy_decisions_websocket),
]

__all__ = ["routes"]
