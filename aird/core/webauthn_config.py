"""WebAuthn relying-party configuration."""

from __future__ import annotations

import os

import tornado.web


def resolve_webauthn_config(handler: tornado.web.RequestHandler) -> tuple[str, str, list[str]]:
    """Return (rp_id, rp_name, allowed_origins)."""
    host = (handler.request.host or "localhost").split(":")[0]
    rp_id = os.environ.get("AIRD_WEBAUTHN_RP_ID", "").strip() or host
    rp_name = os.environ.get("AIRD_WEBAUTHN_RP_NAME", "").strip() or "Aird"
    origin_env = os.environ.get("AIRD_WEBAUTHN_ORIGIN", "").strip()
    if origin_env:
        origins = [part.strip() for part in origin_env.split(",") if part.strip()]
    else:
        origins = [f"{handler.request.protocol}://{handler.request.host}"]
    return rp_id, rp_name, origins
