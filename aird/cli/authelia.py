"""Authelia first/second factor login for CLI sessions."""

from __future__ import annotations

import logging
from typing import Any

import requests

logger = logging.getLogger(__name__)


class AutheliaError(RuntimeError):
    pass


def _needs_second_factor(payload: dict[str, Any]) -> bool:
    if payload.get("status") == "OK" and not payload.get("data"):
        return False
    data = payload.get("data") or {}
    if data.get("methods"):
        return True
    if payload.get("status") in ("OK", "200"):
        return bool(data.get("devices") or data.get("authentication_level") == 1)
    return payload.get("status") in ("Unauthorized", "401")


def second_factor(session: requests.Session, authelia_base: str, totp: str) -> None:
    base = authelia_base.rstrip("/")
    token = totp.strip()
    if not token:
        raise AutheliaError("second_factor_required")
    r = session.post(
        f"{base}/api/secondfactor",
        json={"token": token, "method": "totp"},
        timeout=60,
    )
    if r.status_code == 401:
        raise AutheliaError("Invalid one-time code (TOTP)")
    if r.status_code >= 400:
        raise AutheliaError(f"Authelia second factor failed (HTTP {r.status_code})")


def login(
    session: requests.Session,
    authelia_base: str,
    username: str,
    password: str,
    *,
    totp: str | None = None,
    target_url: str | None = None,
) -> None:
    """
    Complete Authelia login on *session* (sets Authelia cookies).

    Raises AutheliaError on failure. Prompts are handled by the caller; pass *totp*
    when second factor is required.
    """
    base = authelia_base.rstrip("/")
    body: dict[str, Any] = {
        "username": username,
        "password": password,
        "keepMeLoggedIn": False,
    }
    if target_url:
        body["targetURL"] = target_url
        body["requestMethod"] = "GET"

    r = session.post(f"{base}/api/firstfactor", json=body, timeout=60)
    if r.status_code == 401:
        raise AutheliaError("Authelia rejected username or password")
    if r.status_code >= 400:
        raise AutheliaError(f"Authelia first factor failed (HTTP {r.status_code})")

    try:
        payload = r.json()
    except ValueError as exc:
        raise AutheliaError("Authelia returned a non-JSON response") from exc

    if not _needs_second_factor(payload):
        return

    if totp:
        second_factor(session, base, totp)
        return
    raise AutheliaError("second_factor_required")
