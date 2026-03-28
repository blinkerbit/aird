"""Network share startup and lifecycle orchestration."""

from __future__ import annotations

import logging
from typing import Any

from aird.db.network_shares import (
    create_network_share,
    delete_network_share,
    get_all_network_shares,
    update_network_share,
)
from aird.network_share_manager import NetworkShareManager

logger = logging.getLogger(__name__)


class NetworkShareService:
    def build_manager(self) -> NetworkShareManager:
        return NetworkShareManager()

    def auto_start_enabled(self, conn: Any, manager: NetworkShareManager) -> None:
        try:
            enabled_shares = [
                s for s in get_all_network_shares(conn) if s.get("enabled")
            ]
            for share in enabled_shares:
                manager.start_share(share)
            if enabled_shares:
                logger.info("Auto-started %d network share(s)", len(enabled_shares))
        except Exception as ns_err:
            logger.warning("Failed to auto-start network shares: %s", ns_err)

    def list_all(self, conn: Any) -> list[dict[str, Any]]:
        return get_all_network_shares(conn)

    def create(self, conn: Any, *args: Any, **kwargs: Any) -> bool:
        return create_network_share(conn, *args, **kwargs)

    def update(self, conn: Any, share_id: str, **kwargs: Any) -> bool:
        return update_network_share(conn, share_id, **kwargs)

    def delete(self, conn: Any, share_id: str) -> bool:
        return delete_network_share(conn, share_id)
