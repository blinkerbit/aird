"""HTML sanitization for chat messages."""

from __future__ import annotations

import bleach

_ALLOWED_TAGS = ["p", "br", "strong", "em", "a", "code"]
_ALLOWED_ATTRS = {"a": ["href", "title", "rel"]}
_MAX_BODY_CHARS = 32_000


def sanitize_chat_html(raw: str | None) -> str:
    if not raw:
        return ""
    text = str(raw)[:_MAX_BODY_CHARS]
    cleaned = bleach.clean(
        text,
        tags=_ALLOWED_TAGS,
        attributes=_ALLOWED_ATTRS,
        strip=True,
    )
    return cleaned.strip()


def plain_preview(html: str, limit: int = 120) -> str:
    text = bleach.clean(html or "", tags=[], strip=True)
    text = " ".join(text.split())
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"
