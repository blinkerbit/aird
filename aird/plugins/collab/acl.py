"""Public / private visibility for collaboration artifacts."""

from __future__ import annotations

VISIBILITY_PUBLIC = "public"
VISIBILITY_PRIVATE = "private"


def normalize_visibility(raw: str | None) -> str:
    v = str(raw or "").strip().lower()
    return VISIBILITY_PRIVATE if v == VISIBILITY_PRIVATE else VISIBILITY_PUBLIC


def can_access(
    *,
    visibility: str,
    organizer: str,
    username: str | None,
    participants: set[str] | list[str] | None,
) -> bool:
    name = (username or "").strip()
    if not name:
        return False
    org = (organizer or "").strip()
    if name == org:
        return True
    if normalize_visibility(visibility) == VISIBILITY_PUBLIC:
        return True
    pool = set(participants or [])
    return name in pool


def can_manage(*, organizer: str, username: str | None) -> bool:
    name = (username or "").strip()
    return bool(name) and name == (organizer or "").strip()
