"""WebAuthn relying-party configuration."""

from __future__ import annotations

import os

from aird.core.security import effective_request_endpoint


def resolve_webauthn_config(handler) -> tuple[str, str, list[str]]:
    """Return (rp_id, rp_name, allowed_origins)."""
    host, port, protocol = effective_request_endpoint(handler)
    rp_id = os.environ.get("AIRD_WEBAUTHN_RP_ID", "").strip() or host
    rp_name = os.environ.get("AIRD_WEBAUTHN_RP_NAME", "").strip() or "Aird"
    origin_env = os.environ.get("AIRD_WEBAUTHN_ORIGIN", "").strip()
    if origin_env:
        origins = [part.strip() for part in origin_env.split(",") if part.strip()]
    else:
        if port in (80, 443):
            origins = [f"{protocol}://{host}"]
        else:
            origins = [f"{protocol}://{host}:{port}"]
    return rp_id, rp_name, origins
