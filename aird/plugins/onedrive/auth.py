"""Microsoft Graph auth for host OneDrive backup (device code + refresh)."""

from __future__ import annotations

import json
import logging
import time
from typing import Any

import requests

logger = logging.getLogger(__name__)

GRAPH_SCOPES = "Files.ReadWrite offline_access User.Read"
TIMEOUT = 30


class OneDriveAuthError(Exception):
    def __init__(self, message: str, *, pending: bool = False):
        super().__init__(message)
        self.pending = pending


def _token_url(tenant: str) -> str:
    return f"https://login.microsoftonline.com/{tenant or 'common'}/oauth2/v2.0/token"


def _device_url(tenant: str) -> str:
    return f"https://login.microsoftonline.com/{tenant or 'common'}/oauth2/v2.0/devicecode"


def start_device_flow(client_id: str, tenant: str) -> dict[str, Any]:
    if not client_id:
        raise OneDriveAuthError("Azure client ID is required for device login")
    try:
        response = requests.post(
            _device_url(tenant),
            data={"client_id": client_id, "scope": GRAPH_SCOPES},
            timeout=TIMEOUT,
        )
    except requests.RequestException as exc:
        raise OneDriveAuthError(f"Device login request failed: {exc}") from exc
    data = response.json() if response.content else {}
    if response.status_code >= 400 or not data.get("device_code"):
        detail = data.get("error_description") or data.get("error") or response.text[:200]
        raise OneDriveAuthError(detail or "Could not start device login")
    return {
        "device_code": data["device_code"],
        "user_code": data.get("user_code", ""),
        "verification_uri": data.get("verification_uri", ""),
        "expires_in": int(data.get("expires_in") or 900),
        "interval": max(3, int(data.get("interval") or 5)),
    }


def poll_device_flow(client_id: str, tenant: str, device_code: str) -> dict[str, Any]:
    if not client_id or not device_code:
        raise OneDriveAuthError("Device login is not in progress")
    try:
        response = requests.post(
            _token_url(tenant),
            data={
                "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
                "client_id": client_id,
                "device_code": device_code,
            },
            timeout=TIMEOUT,
        )
    except requests.RequestException as exc:
        raise OneDriveAuthError(f"Device login poll failed: {exc}") from exc
    data = response.json() if response.content else {}
    if response.status_code == 200 and data.get("access_token"):
        return _normalize_token_payload(data)
    error = str(data.get("error") or "")
    if error in {"authorization_pending", "slow_down"}:
        raise OneDriveAuthError("Waiting for sign-in", pending=True)
    if error == "expired_token":
        raise OneDriveAuthError("Device code expired — start again")
    detail = data.get("error_description") or error or "Device login failed"
    raise OneDriveAuthError(detail)


def refresh_access_token(client_id: str, tenant: str, refresh_token: str) -> dict[str, Any]:
    if not client_id or not refresh_token:
        raise OneDriveAuthError("Refresh token or client ID missing")
    try:
        response = requests.post(
            _token_url(tenant),
            data={
                "grant_type": "refresh_token",
                "client_id": client_id,
                "refresh_token": refresh_token,
                "scope": GRAPH_SCOPES,
            },
            timeout=TIMEOUT,
        )
    except requests.RequestException as exc:
        raise OneDriveAuthError(f"Token refresh failed: {exc}") from exc
    data = response.json() if response.content else {}
    if response.status_code >= 400 or not data.get("access_token"):
        detail = data.get("error_description") or data.get("error") or "Token refresh failed"
        raise OneDriveAuthError(detail)
    return _normalize_token_payload(data)


def _normalize_token_payload(data: dict) -> dict[str, Any]:
    expires_in = int(data.get("expires_in") or 3600)
    return {
        "access_token": str(data.get("access_token") or ""),
        "refresh_token": str(data.get("refresh_token") or data.get("refreshToken") or ""),
        "expires_at": int(time.time()) + max(60, expires_in),
    }
