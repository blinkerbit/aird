"""Send Web Push notifications to subscribed browsers / installed PWAs."""

from __future__ import annotations

import json
import logging
import sqlite3
from typing import Any

logger = logging.getLogger(__name__)


def _webpush_available() -> bool:
    try:
        import pywebpush  # noqa: F401

        return True
    except ImportError:
        return False


def send_web_push(
    conn: sqlite3.Connection | None,
    username: str,
    payload: dict[str, Any],
) -> int:
    """Send *payload* JSON to all push subscriptions for *username*.

    Returns the number of successful deliveries. Removes gone subscriptions.
    """
    if not conn or not username:
        return 0
    if not _webpush_available():
        logger.debug("pywebpush not installed; skipping web push")
        return 0

    from pywebpush import WebPushException, webpush

    from aird.pwa import store as push_store
    from aird.pwa.vapid import load_or_create_vapid

    subs = push_store.subscriptions_for_user(conn, username)
    if not subs:
        return 0

    vapid = load_or_create_vapid()
    body = json.dumps(payload)
    claims = {"sub": vapid.get("subject") or "mailto:aird@localhost"}
    sent = 0
    for sub in subs:
        try:
            webpush(
                subscription_info=sub,
                data=body,
                vapid_private_key=vapid["private_pem"],
                vapid_claims=claims,
                ttl=60,
            )
            sent += 1
        except WebPushException as exc:
            status = getattr(getattr(exc, "response", None), "status_code", None)
            if status in (404, 410):
                try:
                    push_store.delete_endpoint(conn, sub["endpoint"])
                except Exception:
                    logger.debug("failed to prune push endpoint", exc_info=True)
            else:
                logger.debug("web push failed for %s: %s", username, exc)
        except Exception:
            logger.debug("web push error for %s", username, exc_info=True)
    return sent
