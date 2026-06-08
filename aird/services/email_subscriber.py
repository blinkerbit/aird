"""Send user emails in response to domain events."""

from __future__ import annotations

import logging
import threading
from typing import Callable

import aird.constants as constants
from aird.core.events import ShareCreatedEvent
from aird.services.email_service import EmailService

logger = logging.getLogger(__name__)


class EmailNotificationSubscriber:
    def __init__(
        self,
        email_service: EmailService | None = None,
        db_conn_getter: Callable[[], object | None] | None = None,
    ):
        self._email = email_service or EmailService()
        self._db_conn_getter = db_conn_getter or (lambda: constants.DB_CONN)

    def on_share_created(self, event: ShareCreatedEvent) -> None:
        if not self._email.enabled:
            return
        threading.Thread(
            target=self._deliver_share_created,
            args=(event,),
            daemon=True,
            name="aird-email-share",
        ).start()

    def _deliver_share_created(self, event: ShareCreatedEvent) -> None:
        try:
            recipients = list(event.allowed_users or []) + list(event.modify_users or [])
            sent = self._email.notify_share_created(
                self._db_conn_getter(),
                share_id=event.share_id,
                creator=event.creator,
                recipient_usernames=recipients,
                path_count=event.path_count,
            )
            if sent:
                logger.info(
                    "Sent %s share notification email(s) for %s",
                    sent,
                    event.share_id,
                )
        except Exception:
            logger.exception("Share notification email failed for %s", event.share_id)
