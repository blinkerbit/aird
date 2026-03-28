"""File quota tracking service."""

from __future__ import annotations

from typing import Any

from aird.db.quota import get_user_quota, update_user_used_bytes


class QuotaService:
    def get_quota(self, conn: Any, username: str) -> dict[str, Any]:
        return get_user_quota(conn, username)

    def update_used_bytes(self, conn: Any, username: str, delta_bytes: int) -> None:
        update_user_used_bytes(conn, username, delta_bytes)
