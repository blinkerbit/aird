"""Tag chip color helpers for browse and admin UI."""

from __future__ import annotations

import hashlib
import re
from collections.abc import Iterable

_HEX6 = re.compile(r"^#[0-9a-fA-F]{6}$")
_HEX3 = re.compile(r"^#[0-9a-fA-F]{3}$")

# Distinct chip colors assigned when auto-color is on.
TAG_COLOR_PALETTE: tuple[str, ...] = (
    "#6366f1",
    "#0ea5e9",
    "#10b981",
    "#f59e0b",
    "#ef4444",
    "#8b5cf6",
    "#14b8a6",
    "#f97316",
    "#ec4899",
    "#84cc16",
    "#06b6d4",
    "#a855f7",
    "#eab308",
    "#22c55e",
    "#3b82f6",
)


def normalize_tag_color(color: str | None) -> str | None:
    """Return a normalized #rrggbb color or None if invalid / empty."""
    if not color:
        return None
    c = str(color).strip()
    if _HEX6.fullmatch(c):
        return c.lower()
    if _HEX3.fullmatch(c):
        return "#" + "".join(ch * 2 for ch in c[1:]).lower()
    return None


def tag_chip_inline_style(color: str | None) -> str:
    """Inline style for a colored tag chip; empty string when no valid color."""
    norm = normalize_tag_color(color)
    if not norm:
        return ""
    r = int(norm[1:3], 16)
    g = int(norm[3:5], 16)
    b = int(norm[5:7], 16)
    lum = (0.299 * r + 0.587 * g + 0.114 * b) / 255
    fg = "#111827" if lum > 0.55 else "#f9fafb"
    border = f"color-mix(in oklch, {norm} 65%, transparent)"
    return (
        f"background:{norm};color:{fg};border-color:{border}"
    )


def next_auto_tag_color(used: Iterable[str] | None, tag_name: str = "") -> str:
    """Return the next unused palette color, or a stable hash color if the palette is full."""
    taken = {normalize_tag_color(c) for c in (used or [])}
    taken.discard(None)
    for color in TAG_COLOR_PALETTE:
        if color not in taken:
            return color
    digest = hashlib.md5(str(tag_name or "tag").encode("utf-8"), usedforsecurity=False).hexdigest()
    return f"#{digest[:6]}"
