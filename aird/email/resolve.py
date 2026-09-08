"""Resolve a delivery email address for an Aird username."""

from __future__ import annotations

import re
import sqlite3

from aird.db.user_attributes import get_user_attributes

_EMAIL_RE = re.compile(r"\A[^@\s]+@[^@\s]+\.[^@\s]+\Z")


def looks_like_email(value: str) -> bool:
    text = (value or "").strip()
    if not text or len(text) > 320 or text.count("@") != 1:
        return False
    local, _, domain = text.partition("@")
    if not (1 <= len(local) <= 254):
        return False
    labels = domain.split(".")
    if len(labels) < 2:
        return False
    if any(not (1 <= len(part) <= 63) for part in labels[:-1]):
        return False
    if not (2 <= len(labels[-1]) <= 63):
        return False
    return bool(_EMAIL_RE.match(text))


def resolve_user_email(conn: sqlite3.Connection | None, username: str) -> str | None:
    """
    Return an email for *username*.

    Order: user_attributes ``email`` → username when it is an email address.
    """
    username = (username or "").strip()
    if not username:
        return None
    if conn is not None:
        try:
            attrs = get_user_attributes(conn, username)
            raw = (attrs.get("email") or attrs.get("mail") or "").strip()
            if looks_like_email(raw):
                return raw
        except Exception:
            pass
    if looks_like_email(username):
        return username
    return None
