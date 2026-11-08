"""Shared app chrome helpers — keep the top nav stable across pages."""

from __future__ import annotations


def nav_active_for_path(path: str | None) -> str:
    """Return which main-nav item matches this request path."""
    raw = (path or "").strip() or "/"
    if not raw.startswith("/"):
        raw = "/" + raw
    # Drop trailing slash except root.
    if len(raw) > 1:
        raw = raw.rstrip("/")
    if raw.startswith("/chat"):
        return "chat"
    if raw.startswith("/p2p"):
        return "p2p"
    if raw.startswith("/share"):
        return "share"
    if raw.startswith("/search"):
        return "search"
    if raw.startswith("/profile"):
        return "profile"
    if raw.startswith("/admin"):
        return "admin"
    if raw.startswith("/gitlab"):
        return "gitlab"
    if raw.startswith("/polls"):
        return "polls"
    if raw.startswith("/lists"):
        return "lists"
    if raw.startswith("/meet"):
        return "meet"
    if raw == "/" or raw.startswith("/files"):
        return "browse"
    return ""
