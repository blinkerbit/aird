"""Default subscribers for domain events."""

from __future__ import annotations

import logging
from collections import Counter

from aird.core.events import (
    ShareCreatedEvent,
    TransferStartedEvent,
    UserAuthenticatedEvent,
)

logger = logging.getLogger(__name__)


class EventMetricsSubscriber:
    """Maintains basic in-memory counters for internal events."""

    def __init__(self):
        self._counter: Counter[str] = Counter()

    def on_user_authenticated(self, _event: UserAuthenticatedEvent) -> None:
        self._counter["user_authenticated"] += 1

    def on_share_created(self, _event: ShareCreatedEvent) -> None:
        self._counter["share_created"] += 1

    def on_transfer_started(self, _event: TransferStartedEvent) -> None:
        self._counter["transfer_started"] += 1

    def snapshot(self) -> dict[str, int]:
        return dict(self._counter)


class EventLoggingSubscriber:
    """Writes concise event logs for observability/auditing."""

    def on_user_authenticated(self, event: UserAuthenticatedEvent) -> None:
        logger.info(
            "event=user_authenticated username=%s role=%s ip=%s",
            event.username,
            event.role,
            event.ip,
        )

    def on_share_created(self, event: ShareCreatedEvent) -> None:
        logger.info(
            "event=share_created share_id=%s creator=%s paths=%s",
            event.share_id,
            event.creator,
            event.path_count,
        )

    def on_transfer_started(self, event: TransferStartedEvent) -> None:
        logger.info(
            "event=transfer_started room_id=%s initiator=%s anonymous=%s",
            event.room_id,
            event.initiator,
            event.allow_anonymous,
        )
