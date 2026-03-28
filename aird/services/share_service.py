"""Share-oriented service facade."""

from __future__ import annotations

from typing import Any

from aird.db.shares import (
    cleanup_expired_shares,
    delete_share,
    get_all_shares,
    get_share_by_id,
    get_share_download_count,
    get_shares_for_path,
    insert_share,
    is_share_expired,
    update_share,
)


class ShareService:
    def list_shares(self, conn: Any) -> dict[str, Any]:
        return get_all_shares(conn)

    def get_share(self, conn: Any, share_id: str) -> dict[str, Any] | None:
        return get_share_by_id(conn, share_id)

    def get_shares_for_path(self, conn: Any, path: str) -> list[dict[str, Any]]:
        return get_shares_for_path(conn, path)

    def get_download_count(self, conn: Any, share_id: str) -> int:
        return get_share_download_count(conn, share_id)

    def cleanup_expired(self, conn: Any) -> int:
        return cleanup_expired_shares(conn)

    def insert_share(self, conn: Any, *args: Any, **kwargs: Any) -> bool:
        return insert_share(conn, *args, **kwargs)

    def delete_share(self, conn: Any, share_id: str) -> bool:
        return delete_share(conn, share_id)

    def update_share(self, conn: Any, share_id: str, **kwargs: Any) -> bool:
        return update_share(conn, share_id, **kwargs)

    def is_expired(self, expiry_date: Any) -> bool:
        return is_share_expired(expiry_date)
