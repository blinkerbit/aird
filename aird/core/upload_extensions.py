"""Normalize admin-defined upload extensions."""

from __future__ import annotations

import re

_EXT_RE = re.compile(r"\.[a-z0-9]{1,16}\Z")
_SPLIT_RE = re.compile(r"[\s,]+")
INVALID_UPLOAD_EXTENSION = "Invalid file extension"


def normalize_upload_extension(raw: str) -> str | None:
    """Return a lowercase dotted extension, or None when the token is not allowed."""
    token = (raw or "").strip().lower()
    if not token:
        return None
    if not token.startswith("."):
        token = "." + token
    if _EXT_RE.fullmatch(token) is None:
        return None
    return token


def collect_upload_extensions(
    selected: list[str], custom_raw: str
) -> tuple[set[str], str | None]:
    """Union checkbox values with a custom list. Invalid custom tokens are an error."""
    chosen: set[str] = set()
    for item in selected:
        norm = normalize_upload_extension(item)
        if norm is None:
            return set(), INVALID_UPLOAD_EXTENSION
        chosen.add(norm)
    raw = (custom_raw or "").strip()
    if not raw:
        return chosen, None
    for part in _SPLIT_RE.split(raw):
        if not part:
            continue
        norm = normalize_upload_extension(part)
        if norm is None:
            return set(), INVALID_UPLOAD_EXTENSION
        chosen.add(norm)
    return chosen, None
