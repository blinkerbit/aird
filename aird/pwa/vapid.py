"""VAPID key management for Web Push."""

from __future__ import annotations

import base64
import json
import logging
from pathlib import Path

from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from aird.core.auth_secrets import secrets_dir_for_root

logger = logging.getLogger(__name__)

_VAPID_FILENAME = "vapid.json"
_cached: dict | None = None


def _secrets_path(root_dir: str | None = None) -> Path:
    from aird import constants

    root = root_dir or getattr(constants, "ROOT_DIR", ".") or "."
    return secrets_dir_for_root(root) / _VAPID_FILENAME


def _generate_vapid_keys() -> dict:
    from py_vapid import Vapid

    vapid = Vapid()
    vapid.generate_keys()
    private_pem = vapid.private_pem()
    if isinstance(private_pem, bytes):
        private_pem = private_pem.decode("utf-8")
    public_raw = vapid.public_key.public_bytes(Encoding.X962, PublicFormat.UncompressedPoint)
    public_b64 = base64.urlsafe_b64encode(public_raw).decode("ascii").rstrip("=")
    return {
        "private_pem": private_pem,
        "public_key": public_b64,
        "subject": "mailto:aird@localhost",
    }


def load_or_create_vapid(root_dir: str | None = None) -> dict:
    """Return VAPID dict with public_key, private_pem, subject. Cached in-process."""
    global _cached
    if _cached is not None:
        return _cached
    path = _secrets_path(root_dir)
    if path.is_file():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if data.get("public_key") and data.get("private_pem"):
                _cached = data
                return data
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("Invalid VAPID file %s: %s", path, exc)
    data = _generate_vapid_keys()
    try:
        path.write_text(json.dumps(data), encoding="utf-8")
        try:
            path.chmod(0o600)
        except OSError:
            pass
    except OSError as exc:
        logger.warning("Could not persist VAPID keys: %s", exc)
    _cached = data
    return data


def vapid_public_key(root_dir: str | None = None) -> str:
    return load_or_create_vapid(root_dir)["public_key"]


def clear_vapid_cache() -> None:
    global _cached
    _cached = None
