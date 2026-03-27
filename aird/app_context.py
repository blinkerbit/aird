"""Application dependency container (composition root output)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class AppContext:
    """Holds runtime dependencies shared across handlers/services.

    Keeping these dependencies explicit avoids hidden coupling through
    module-level globals and makes unit testing easier.
    """

    db_conn: Any = None
    feature_flags: dict[str, Any] = field(default_factory=dict)
    cloud_manager: Any = None
    network_share_manager: Any = None
    room_manager: Any = None
    event_bus: Any = None
    event_metrics: Any = None
    repositories: dict[str, Any] = field(default_factory=dict)
    services: dict[str, Any] = field(default_factory=dict)

    def get_repo(self, name: str, default: Any = None) -> Any:
        return self.repositories.get(name, default)

    def get_service(self, name: str, default: Any = None) -> Any:
        return self.services.get(name, default)
