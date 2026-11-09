"""Insert collaboration card messages into chat."""

from __future__ import annotations

import json
from typing import Any

from aird.plugins.chat import db as chat_db
from aird.plugins.chat.notify import dispatch_message


def card_metadata(kind: str, artifact_id: str, *, title: str = "", snapshot: dict | None = None) -> dict:
    meta: dict[str, Any] = {
        "card": {
            "kind": kind,
            "id": artifact_id,
            "title": title,
            "snapshot": snapshot or {},
        }
    }
    return meta


def share_card_to_conversation(
    conn,
    *,
    username: str,
    user_id: int | None,
    conversation_id: str,
    kind: str,
    artifact_id: str,
    title: str,
    snapshot: dict | None = None,
    reply_to_id: str | None = None,
    collab=None,
) -> dict:
    if collab is not None:
        from aird.plugins.collab.live import pin_for_chat

        pin_for_chat(
            collab,
            conversation_id=conversation_id,
            kind=kind,
            artifact_id=artifact_id,
            username=username,
        )
    msg = chat_db.insert_message(
        conn,
        username=username,
        conversation_id=conversation_id,
        msg_type="card",
        body="",
        metadata=card_metadata(kind, artifact_id, title=title, snapshot=snapshot),
        reply_to_id=reply_to_id,
    )
    dispatch_message(msg, sender_id=user_id, sender_username=username)
    return msg


def parse_card_ref(metadata: dict | None) -> dict | None:
    if not isinstance(metadata, dict):
        return None
    card = metadata.get("card")
    if not isinstance(card, dict):
        return None
    kind = str(card.get("kind") or "").strip()
    aid = str(card.get("id") or "").strip()
    if not kind or not aid:
        return None
    return card
