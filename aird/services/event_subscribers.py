"""Default subscribers for domain events."""

from __future__ import annotations

import logging
import threading
from collections import Counter

from aird.core.events import (
    PolicyDecisionEvent,
    ShareCreatedEvent,
    TransferStartedEvent,
    UserAuthenticatedEvent,
)

logger = logging.getLogger(__name__)


class EventMetricsSubscriber:
    """Maintains basic in-memory counters for internal events."""

    def __init__(self):
        self._counter: Counter[str] = Counter()
        self._lock = threading.Lock()

    def on_user_authenticated(self, _event: UserAuthenticatedEvent) -> None:
        with self._lock:
            self._counter["user_authenticated"] += 1

    def on_share_created(self, _event: ShareCreatedEvent) -> None:
        with self._lock:
            self._counter["share_created"] += 1

    def on_transfer_started(self, _event: TransferStartedEvent) -> None:
        with self._lock:
            self._counter["transfer_started"] += 1

    def snapshot(self) -> dict[str, int]:
        with self._lock:
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

    def on_policy_decision(self, event: PolicyDecisionEvent) -> None:
        logger.info(
            "event=policy_decision username=%s action=%s decision=%s policy=%s reason=%s",
            event.username,
            event.action,
            event.decision,
            event.matched_policy_name,
            event.reason,
        )


class PolicyDecisionMetricsSubscriber:
    """Counts policy decisions by effect."""

    def __init__(self) -> None:
        self._counter: Counter[str] = Counter()

    def on_policy_decision(self, event: PolicyDecisionEvent) -> None:
        self._counter[f"policy_{event.decision}"] += 1
        self._counter[f"policy_action_{event.action}"] += 1

    def snapshot(self) -> dict[str, int]:
        return dict(self._counter)
