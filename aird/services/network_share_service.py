"""Network share startup and lifecycle orchestration."""

from __future__ import annotations

import logging
from typing import Any

from aird.network_share_manager import NetworkShareManager
from aird.repositories.db_repositories import NetworkShareRepository

logger = logging.getLogger(__name__)


class NetworkShareService:
    def __init__(self, repo: NetworkShareRepository):
        self.repo = repo

    def build_manager(self) -> NetworkShareManager:
        return NetworkShareManager()

    def auto_start_enabled(self, conn: Any, manager: NetworkShareManager) -> None:
        try:
            enabled_shares = [s for s in self.repo.list_all(conn) if s.get("enabled")]
            for share in enabled_shares:
                manager.start_share(share)
            if enabled_shares:
                logger.info("Auto-started %d network share(s)", len(enabled_shares))
        except Exception as ns_err:
            logger.warning("Failed to auto-start network shares: %s", ns_err)
