"""User favorites service."""

from __future__ import annotations

from typing import Any

from aird.db.favorites import get_user_favorites, toggle_favorite


class FavoritesService:
    def toggle(self, conn: Any, username: str, path: str) -> bool:
        return toggle_favorite(conn, username, path)

    def get_favorites(self, conn: Any, username: str) -> list[str]:
        return get_user_favorites(conn, username)
