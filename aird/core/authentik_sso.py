"""Authentik auto-login config and the login decision.

Auto-login proceeds when the setting is on and the username header is an
existing active AIRD user. A stored shared secret must still match. If none
is stored, the username header is enough. It never creates users.
"""

from __future__ import annotations

import logging
import re
import secrets

from aird.db.config import load_json_server_config, save_json_server_config

logger = logging.getLogger(__name__)

CONFIG_KEY = "authentik_sso"
DEFAULT_USERNAME_HEADER = "X-authentik-username"
SECRET_HEADER = "X-Aird-Authentik-Secret"
ACTION_FORM = "form"
ACTION_FORBIDDEN = "forbidden"
ACTION_SESSION = "session"

ACCESS_DENIED_HTML = (
    "<!DOCTYPE html><html lang=\"en\"><head><meta charset=\"utf-8\">"
    "<title>Access denied</title></head>"
    "<body><p>Access is not allowed.</p></body></html>"
)

_HEADER_RE = re.compile(r"^X-[A-Za-z0-9][A-Za-z0-9_-]{0,120}$")
_SECRET_MAX_LEN = 512


class AuthentikLoginDecision:
    def __init__(self, action: str, user: dict | None = None):
        self.action = action
        self.user = user


def validate_username_header(name: str) -> str:
    """Accept only an X-* header name. Cookie and Authorization are rejected."""
    cleaned = (name or "").strip()
    if cleaned[:1] in {"x", "X"}:
        cleaned = "X" + cleaned[1:]
    if not _HEADER_RE.fullmatch(cleaned):
        raise ValueError("Username header must be an X-* header name")
    if cleaned.lower() == SECRET_HEADER.lower():
        raise ValueError("Username header must be an X-* header name")
    return cleaned


def default_authentik_sso() -> dict:
    return {
        "enabled": False,
        "username_header": DEFAULT_USERNAME_HEADER,
        "secret": "",
    }


def public_authentik_sso(config: dict | None) -> dict:
    """Admin view. Never includes the raw secret."""
    cfg = config or {}
    header = str(cfg.get("username_header") or DEFAULT_USERNAME_HEADER)
    try:
        header = validate_username_header(header)
    except ValueError:
        header = DEFAULT_USERNAME_HEADER
    return {
        "enabled": bool(cfg.get("enabled")),
        "username_header": header,
        "secret_set": bool(cfg.get("secret")),
    }


def load_authentik_sso(conn) -> dict:
    raw = load_json_server_config(conn, CONFIG_KEY) if conn is not None else {}
    header = str(raw.get("username_header") or DEFAULT_USERNAME_HEADER).strip()
    try:
        header = validate_username_header(header)
    except ValueError:
        header = DEFAULT_USERNAME_HEADER
    secret = raw.get("secret")
    if not isinstance(secret, str):
        secret = ""
    return {
        "enabled": bool(raw.get("enabled")),
        "username_header": header,
        "secret": secret,
    }


def merge_authentik_sso(current: dict, payload: dict) -> dict:
    """Merge an admin save onto the stored config. Raises ValueError to reject."""
    current = current or default_authentik_sso()
    if not isinstance(payload, dict):
        raise ValueError("Invalid Authentik settings")

    if "enabled" in payload:
        enabled = _as_bool(payload.get("enabled"))
    else:
        enabled = bool(current.get("enabled"))

    if "username_header" in payload and str(payload.get("username_header") or "").strip():
        header = validate_username_header(str(payload.get("username_header")))
    else:
        header = validate_username_header(
            str(current.get("username_header") or DEFAULT_USERNAME_HEADER)
        )

    secret = str(current.get("secret") or "")
    if "secret" in payload and payload.get("secret") is not None:
        submitted = payload.get("secret")
        if not isinstance(submitted, str):
            raise ValueError("Secret must be a string")
        if len(submitted) > _SECRET_MAX_LEN:
            raise ValueError("Secret is too long")
        stripped = submitted.strip()
        if stripped:
            secret = stripped

    return {
        "enabled": enabled,
        "username_header": header,
        "secret": secret,
    }


def save_authentik_sso(conn, payload: dict) -> dict:
    current = load_authentik_sso(conn)
    updated = merge_authentik_sso(current, payload)
    save_json_server_config(
        conn,
        CONFIG_KEY,
        {
            "enabled": updated["enabled"],
            "username_header": updated["username_header"],
            "secret": updated["secret"],
        },
    )
    return updated


def decide_authentik_login(config: dict, headers, lookup_user) -> AuthentikLoginDecision:
    """Return form, forbidden, or session. Does not set cookies or create users."""
    cfg = config or default_authentik_sso()
    if not cfg.get("enabled"):
        return AuthentikLoginDecision(ACTION_FORM)

    stored = cfg.get("secret") if isinstance(cfg.get("secret"), str) else ""
    if stored:
        provided = _header_value(headers, SECRET_HEADER)
        if not _secrets_match(provided, stored):
            logger.warning("Authentik auto-login rejected: shared secret missing or mismatch")
            return AuthentikLoginDecision(ACTION_FORM)

    try:
        header_name = validate_username_header(
            str(cfg.get("username_header") or DEFAULT_USERNAME_HEADER)
        )
    except ValueError:
        logger.warning("Authentik auto-login skipped: username header is not an X-* name")
        return AuthentikLoginDecision(ACTION_FORM)

    username = _header_value(headers, header_name)
    if not username:
        return AuthentikLoginDecision(ACTION_FORM)

    try:
        user = lookup_user(username) if lookup_user is not None else None
    except Exception:
        logger.warning("Authentik auto-login user lookup failed", exc_info=True)
        return AuthentikLoginDecision(ACTION_FORM)

    if not isinstance(user, dict) or not user.get("active") or not user.get("username"):
        logger.warning("Authentik auto-login denied: user missing or inactive")
        return AuthentikLoginDecision(ACTION_FORBIDDEN)

    return AuthentikLoginDecision(
        ACTION_SESSION,
        {
            "id": user.get("id"),
            "username": user.get("username"),
            "role": user.get("role") or "user",
            "active": True,
            "must_change_password": bool(user.get("must_change_password")),
        },
    )


def _as_bool(value) -> bool:
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "on", "yes"}
    return bool(value)


def _secrets_match(provided: str, stored: str) -> bool:
    if not provided or not stored:
        return False
    try:
        return secrets.compare_digest(provided.encode("utf-8"), stored.encode("utf-8"))
    except Exception:
        return False


def _header_value(headers, name: str) -> str:
    if not headers or not name:
        return ""
    found = None
    seen = False
    try:
        pairs = list(headers.items())
    except Exception:
        pairs = []
    target = name.lower()
    for key, item in pairs:
        if str(key).lower() == target:
            found = item
            seen = True
            break
    if not seen:
        getter = getattr(headers, "get", None)
        if getter is None:
            return ""
        found = getter(name)
        if found is None:
            return ""
    if isinstance(found, (list, tuple)):
        found = found[0] if found else ""
    if isinstance(found, (bytes, bytearray)):
        found = found.decode("utf-8", "replace")
    return str(found).strip()
