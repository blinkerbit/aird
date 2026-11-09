"""Send a list reminder once, to everyone the list is shared with."""

from __future__ import annotations

import logging

from aird.plugins.collab.live import notify_event
from aird.plugins.collab.schema import collab_conn
from aird.plugins.collab import lists_db
from aird.utils.util import is_feature_enabled

logger = logging.getLogger(__name__)

PLUGIN_FLAG = "reminders"


def is_reminders_enabled() -> bool:
    return is_feature_enabled(PLUGIN_FLAG, False)


def fire_due_reminders() -> int:
    if not is_reminders_enabled():
        return 0
    conn = collab_conn()
    if conn is None:
        return 0
    try:
        due = lists_db.claim_due_reminders(conn)
        for row in due:
            text = str(row.get("text") or "Item").strip()
            notify_event(
                conn,
                kind="list",
                artifact_id=str(row.get("list_id") or ""),
                actor="",
                preview=f"{text} is due",
            )
        return len(due)
    except Exception:
        logger.debug("list reminders failed", exc_info=True)
        return 0
    finally:
        conn.close()
