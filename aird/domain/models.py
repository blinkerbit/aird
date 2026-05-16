"""Explicit domain model objects."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass(frozen=True)
class UserIdentity:
    username: str
    role: str = "user"
    is_anonymous: bool = False


# ---------------------------------------------------------------------------
# ABAC dimensions
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SubjectAttributes:
    """User dimension (the requester)."""

    username: str
    role: str = "user"
    clearance: str = "internal"  # public | internal | pii_authorized | admin
    groups: tuple[str, ...] = ()
    quota_bytes: int | None = None
    extra: tuple[tuple[str, str], ...] = ()  # frozen view of arbitrary attrs

    def attrs(self) -> dict[str, Any]:
        """Flat dict suitable for AST attribute lookup."""
        out: dict[str, Any] = {
            "username": self.username,
            "role": self.role,
            "clearance": self.clearance,
            "groups": list(self.groups),
            "quota_bytes": self.quota_bytes,
        }
        for key, value in self.extra:
            out.setdefault(key, value)
        return out


@dataclass(frozen=True)
class ResourceAttributes:
    """Resource dimension (the thing being accessed)."""

    path: str | None = None
    tags: tuple[str, ...] = ()
    size: int | None = None
    extension: str | None = None

    def attrs(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "tags": list(self.tags),
            "size": self.size,
            "extension": self.extension,
        }


@dataclass(frozen=True)
class EnvironmentContext:
    """Environment dimension (when / where / how)."""

    timestamp: datetime
    ip: str | None = None
    is_corporate_ip: bool = False
    is_managed_device: bool = False

    def attrs(self) -> dict[str, Any]:
        return {
            "timestamp": self.timestamp.isoformat() if self.timestamp else None,
            "ip": self.ip,
            "is_corporate_ip": self.is_corporate_ip,
            "is_managed_device": self.is_managed_device,
        }


@dataclass(frozen=True)
class AccessRequest:
    """Bundled inputs handed to the PDP."""

    subject: SubjectAttributes
    action: str
    resource: ResourceAttributes
    environment: EnvironmentContext

    def to_attrs(self) -> dict[str, Any]:
        return {
            "subject": self.subject.attrs(),
            "action": self.action,
            "resource": self.resource.attrs(),
            "environment": self.environment.attrs(),
        }


@dataclass(frozen=True)
class AccessDecision:
    """PDP output."""

    effect: str  # "permit" | "deny"
    reason: str
    matched_policy_id: int | None = None
    matched_policy_name: str | None = None

    @property
    def is_permit(self) -> bool:
        return self.effect == "permit"

    @property
    def is_deny(self) -> bool:
        return self.effect == "deny"


@dataclass(frozen=True)
class ShareRecord:
    share_id: str
    paths: tuple[str, ...]
    allowed_users: tuple[str, ...] = field(default_factory=tuple)
    share_type: str = "static"
    has_token: bool = False

    @classmethod
    def from_mapping(cls, data: dict[str, Any]) -> "ShareRecord":
        return cls(
            share_id=str(data.get("id", "")),
            paths=tuple(data.get("paths", []) or []),
            allowed_users=tuple(data.get("allowed_users", []) or []),
            share_type=str(data.get("share_type", "static")),
            has_token=data.get("secret_token") is not None,
        )


@dataclass(frozen=True)
class TransferSession:
    room_id: str
    creator_peer_id: str
    allow_anonymous: bool = False
    file_name: str | None = None
    file_size: int | None = None
