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


def _prune_gone_subscription(conn, sub: dict, exc) -> None:
    from pywebpush import WebPushException

    if not isinstance(exc, WebPushException):
        return
    status = getattr(getattr(exc, "response", None), "status_code", None)
    if status not in (404, 410):
        return
    from aird.pwa import store as push_store

    try:
        push_store.delete_endpoint(conn, sub["endpoint"])
    except Exception:
        logger.debug("failed to prune push endpoint", exc_info=True)


def _try_send_one_subscription(
    conn,
    username: str,
    sub: dict,
    *,
    body: str,
    vapid_private_key: str,
    claims: dict,
) -> bool:
    from pywebpush import WebPushException, webpush

    try:
        webpush(
            subscription_info=sub,
            data=body,
            vapid_private_key=vapid_private_key,
            vapid_claims=claims,
            ttl=60,
        )
        return True
    except WebPushException as exc:
        _prune_gone_subscription(conn, sub, exc)
        if getattr(getattr(exc, "response", None), "status_code", None) not in (
            404,
            410,
        ):
            logger.debug("web push failed for %s: %s", username, exc)
    except Exception:
        logger.debug("web push error for %s", username, exc_info=True)
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

    from aird.pwa import store as push_store
    from aird.pwa.vapid import load_or_create_vapid

    subs = push_store.subscriptions_for_user(conn, username)
    if not subs:
        return 0

    vapid = load_or_create_vapid()
    body = json.dumps(payload)
    claims = {"sub": vapid.get("subject") or "mailto:aird@localhost"}
    return sum(
        1
        for sub in subs
        if _try_send_one_subscription(
            conn,
            username,
            sub,
            body=body,
            vapid_private_key=vapid["private_pem"],
            claims=claims,
        )
    )
