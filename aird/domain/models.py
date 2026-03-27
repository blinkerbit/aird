"""Explicit domain model objects."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class UserIdentity:
    username: str
    role: str = "user"
    is_anonymous: bool = False


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
