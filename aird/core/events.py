"""Internal domain event bus and event contracts."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any, Callable

logger = logging.getLogger(__name__)

EventHandler = Callable[[Any], None]


class EventBus:
    """Simple in-process pub/sub event bus."""

    def __init__(self):
        self._subscribers: dict[type, list[EventHandler]] = {}

    def subscribe(self, event_type: type, handler: EventHandler) -> None:
        self._subscribers.setdefault(event_type, []).append(handler)

    def publish(self, event: Any) -> None:
        handlers = self._subscribers.get(type(event), [])
        for handler in handlers:
            try:
                handler(event)
            except Exception:
                logger.exception(
                    "Event subscriber failure for %s", type(event).__name__
                )


@dataclass(frozen=True)
class UserAuthenticatedEvent:
    username: str
    role: str
    ip: str
    authenticated_at: float


@dataclass(frozen=True)
class ShareCreatedEvent:
    share_id: str
    creator: str
    path_count: int
    created_at: float


@dataclass(frozen=True)
class TransferStartedEvent:
    room_id: str
    initiator: str
    allow_anonymous: bool
    started_at: float


def now_ts() -> float:
    return time.time()
