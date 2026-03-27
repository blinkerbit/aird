"""Typed DTO contracts for application boundaries."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class ShareCreateRequest:
    paths: list[Any] = field(default_factory=list)
    allowed_users: list[str] = field(default_factory=list)
    modify_users: list[str] = field(default_factory=list)
    share_type: str = "static"
    allow_list: list[str] = field(default_factory=list)
    avoid_list: list[str] = field(default_factory=list)
    disable_token: bool = False
    expiry_date: str | None = None

    @classmethod
    def from_payload(cls, data: dict[str, Any]) -> "ShareCreateRequest":
        return cls(
            paths=list(data.get("paths", []) or []),
            allowed_users=list(data.get("allowed_users", []) or []),
            modify_users=list(data.get("modify_users", []) or []),
            share_type=str(data.get("share_type", "static")),
            allow_list=list(data.get("allow_list", []) or []),
            avoid_list=list(data.get("avoid_list", []) or []),
            disable_token=bool(data.get("disable_token", False)),
            expiry_date=data.get("expiry_date"),
        )


@dataclass(frozen=True)
class ShareCreateResponse:
    share_id: str
    url: str
    secret_token: str | None = None

    def to_json(self) -> dict[str, Any]:
        payload = {"id": self.share_id, "url": self.url}
        if self.secret_token:
            payload["secret_token"] = self.secret_token
        return payload


@dataclass(frozen=True)
class AuthRequest:
    username: str = ""
    password: str = ""
    token: str = ""
    ip: str = ""

    @classmethod
    def from_handler(cls, handler: Any) -> "AuthRequest":
        return cls(
            username=handler.get_argument("username", "").strip(),
            password=handler.get_argument("password", ""),
            token=handler.get_argument("token", "").strip(),
            ip=getattr(handler.request, "remote_ip", "") or "",
        )
