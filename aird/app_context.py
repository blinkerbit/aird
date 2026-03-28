"""Application dependency container (composition root output)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from aird.core.events import EventBus
from aird.services.audit_service import AuditService
from aird.services.config_service import ConfigService
from aird.services.event_subscribers import EventMetricsSubscriber
from aird.services.favorites_service import FavoritesService
from aird.services.network_share_service import NetworkShareService
from aird.services.p2p_service import P2PSignalingService
from aird.services.quota_service import QuotaService
from aird.services.share_service import ShareService
from aird.services.user_service import UserService


@dataclass
class AppContext:
    """Holds runtime dependencies shared across handlers/services."""

    db_conn: Any = None
    feature_flags: dict[str, Any] = field(default_factory=dict)
    cloud_manager: Any = None
    network_share_manager: Any = None
    room_manager: Any = None
    event_bus: EventBus | None = None
    event_metrics: EventMetricsSubscriber | None = None
    services: dict[str, Any] = field(default_factory=dict)

    def get_service(self, name: str, default: Any = None) -> Any:
        return self.services.get(name, default)

    @property
    def audit_service(self) -> AuditService | None:
        return self.services.get("audit_service")

    @property
    def config_service(self) -> ConfigService | None:
        return self.services.get("config_service")

    @property
    def favorites_service(self) -> FavoritesService | None:
        return self.services.get("favorites_service")

    @property
    def network_share_service(self) -> NetworkShareService | None:
        return self.services.get("network_share_service")

    @property
    def p2p_signaling_service(self) -> P2PSignalingService | None:
        return self.services.get("p2p_signaling_service")

    @property
    def quota_service(self) -> QuotaService | None:
        return self.services.get("quota_service")

    @property
    def share_service(self) -> ShareService | None:
        return self.services.get("share_service")

    @property
    def user_service(self) -> UserService | None:
        return self.services.get("user_service")
