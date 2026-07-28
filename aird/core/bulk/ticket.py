"""HMAC bulk transfer tickets (short-lived, bound to user/path/size/op)."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from dataclasses import dataclass
from typing import Any

BULK_TICKET_TTL_SEC = 300
_SIG_LEN = 32


@dataclass(frozen=True)
class BulkTicketClaims:
    op: str
    username: str
    size: int
    exp: int
    upload_dir: str = ""
    filename: str = ""
    relpath: str = ""
    remote_ip: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "op": self.op,
            "username": self.username,
            "size": int(self.size),
            "exp": int(self.exp),
            "upload_dir": self.upload_dir,
            "filename": self.filename,
            "relpath": self.relpath,
            "remote_ip": self.remote_ip,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> BulkTicketClaims:
        op = str(data.get("op", "")).upper()
        if op not in ("PUT", "GET"):
            raise ValueError("invalid op")
        username = str(data.get("username", "")).strip()
        if not username:
            raise ValueError("missing username")
        size = int(data.get("size", 0))
        if size < 0:
            raise ValueError("invalid size")
        exp = int(data.get("exp", 0))
        return cls(
            op=op,
            username=username,
            size=size,
            exp=exp,
            upload_dir=str(data.get("upload_dir", "") or ""),
            filename=str(data.get("filename", "") or ""),
            relpath=str(data.get("relpath", "") or ""),
            remote_ip=data.get("remote_ip"),
        )


def _b64url_encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _b64url_decode(token: str) -> bytes:
    pad = "=" * (-len(token) % 4)
    return base64.urlsafe_b64decode(token + pad)


def mint_bulk_ticket(secret: str, claims: BulkTicketClaims) -> str:
    payload = json.dumps(claims.to_dict(), separators=(",", ":"), sort_keys=True).encode(
        "utf-8"
    )
    sig = hmac.new(secret.encode("utf-8"), payload, hashlib.sha256).digest()
    return _b64url_encode(payload + sig)


def verify_bulk_ticket(secret: str, token: str, *, now: float | None = None) -> BulkTicketClaims:
    raw = _b64url_decode(token.strip())
    if len(raw) < _SIG_LEN + 1:
        raise ValueError("ticket too short")
    payload = raw[:-_SIG_LEN]
    sig = raw[-_SIG_LEN:]
    expected = hmac.new(secret.encode("utf-8"), payload, hashlib.sha256).digest()
    if not hmac.compare_digest(sig, expected):
        raise ValueError("bad ticket signature")
    data = json.loads(payload.decode("utf-8"))
    claims = BulkTicketClaims.from_dict(data)
    ts = time.time() if now is None else now
    if claims.exp < ts:
        raise ValueError("ticket expired")
    if claims.op == "PUT" and not claims.filename:
        raise ValueError("PUT requires filename")
    if claims.op == "GET" and not claims.relpath:
        raise ValueError("GET requires relpath")
    return claims


def default_expiry(now: float | None = None) -> int:
    ts = time.time() if now is None else now
    return int(ts) + BULK_TICKET_TTL_SEC
