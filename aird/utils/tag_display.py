"""Tag chip color helpers for browse and admin UI."""

from __future__ import annotations

import re

_HEX6 = re.compile(r"^#[0-9a-fA-F]{6}$")
_HEX3 = re.compile(r"^#[0-9a-fA-F]{3}$")


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
