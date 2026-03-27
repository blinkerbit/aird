"""Share-oriented read service used by API/handlers."""

from __future__ import annotations

from typing import Any

from aird.repositories.db_repositories import ShareRepository


class ShareService:
    def __init__(self, repo: ShareRepository):
        self.repo = repo

    def list_shares(self, conn: Any) -> dict[str, Any]:
        return self.repo.list_all(conn)

    def get_share(self, conn: Any, share_id: str) -> dict[str, Any] | None:
        return self.repo.get_by_id(conn, share_id)

    def get_shares_for_path(self, conn: Any, path: str) -> list[dict[str, Any]]:
        return self.repo.list_for_path(conn, path)

    def cleanup_expired(self, conn: Any) -> int:
        return self.repo.cleanup_expired(conn)
