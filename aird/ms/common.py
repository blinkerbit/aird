"""Shared microservice helpers: health JSON + Caddy identity headers."""

from __future__ import annotations

import json
import os
import secrets
import time
from typing import Any

# Caddy must inject these after authentication. Backends trust them only on
# the private mesh (never expose Granian/socketify ports publicly).
HEADER_USER = "x-aird-user"
HEADER_ROLE = "x-aird-role"
HEADER_REQUEST_ID = "x-request-id"
HEADER_MESH = "x-aird-mesh"


def identity_from_headers(headers: dict[str, str] | Any) -> dict[str, str]:
    """Normalize identity forwarded by Caddy (case-insensitive)."""
    def get(name: str) -> str:
        if hasattr(headers, "get"):
            for key in (name, name.title(), name.upper()):
                val = headers.get(key)
                if val:
                    return str(val).strip()
        if isinstance(headers, dict):
            lower = {str(k).lower(): v for k, v in headers.items()}
            return str(lower.get(name, "") or "").strip()
        return ""

    return {
        "username": get(HEADER_USER),
        "role": get(HEADER_ROLE) or "user",
        "request_id": get(HEADER_REQUEST_ID),
    }


def require_user(headers: dict[str, str] | Any) -> str:
    user = identity_from_headers(headers)["username"]
    if not user:
        raise PermissionError("missing Caddy identity (X-Aird-User)")
    return user


def health_payload(
    *,
    service: str,
    ready: bool = True,
    checks: dict[str, str] | None = None,
) -> tuple[dict[str, Any], int]:
    body = {
        "status": "ok" if ready else "error",
        "service": service,
        "ts": int(time.time()),
        "checks": checks or {},
    }
    return body, (200 if ready else 503)


def health_json_bytes(service: str, ready: bool = True, checks: dict[str, str] | None = None) -> tuple[bytes, int]:
    body, code = health_payload(service=service, ready=ready, checks=checks)
    return json.dumps(body).encode("utf-8"), code


def mesh_shared_secret() -> str:
    """Shared secret Caddy adds so backends reject direct public hits."""
    return os.environ.get("AIRD_MESH_SECRET", "").strip()


def insecure_mesh_allowed() -> bool:
    """Empty mesh is allowed only with an explicit development opt-in."""
    return (
        os.environ.get("AIRD_ALLOW_INSECURE_MESH", "").strip() == "1"
        or os.environ.get("AIRD_DEV_OPEN_AUTH", "").strip() == "1"
    )


def require_mesh_configuration(service: str) -> None:
    """Fail closed unless a mesh secret is configured or insecure mode is opted in."""
    if mesh_shared_secret() or insecure_mesh_allowed():
        return
    raise SystemExit(
        f"{service}: AIRD_MESH_SECRET is required "
        "(or set AIRD_ALLOW_INSECURE_MESH=1 / AIRD_DEV_OPEN_AUTH=1 for local development)"
    )


def mesh_secret_ok(headers: dict[str, str] | Any) -> bool:
    expected = mesh_shared_secret()
    if not expected:
        return insecure_mesh_allowed()

    def get(name: str) -> str:
        if hasattr(headers, "get"):
            return str(
                headers.get(name)
                or headers.get(name.title())
                or headers.get(name.upper())
                or ""
            ).strip()
        return ""

    provided = get(HEADER_MESH)
    return secrets.compare_digest(provided, expected)
