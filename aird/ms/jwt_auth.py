"""Small HS256 JWT implementation for the Aird auth service."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import time
from typing import Any

from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from aird.ms.ctx import get_state

_ISSUER = "aird-auth"
_AUDIENCE = "aird"
_ACCESS_TTL = 15 * 60
_REFRESH_TTL = 30 * 24 * 60 * 60


def _b64encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _b64decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def _secret(settings: dict[str, Any]) -> bytes:
    configured = os.environ.get("AIRD_JWT_SECRET", "").strip()
    if configured:
        return configured.encode("utf-8")
    if os.environ.get("AIRD_DEV_OPEN_AUTH", "").strip() == "1":
        fallback = settings.get("cookie_secret")
        if isinstance(fallback, bytes):
            return fallback
        if fallback:
            return str(fallback).encode("utf-8")
    raise RuntimeError("AIRD_JWT_SECRET is required outside development")


def encode_token(
    settings: dict[str, Any],
    *,
    username: str,
    role: str,
    token_type: str,
    ttl: int,
    session_id: str | None = None,
) -> str:
    now = int(time.time())
    header = {"alg": "HS256", "typ": "JWT"}
    payload = {
        "iss": _ISSUER,
        "aud": _AUDIENCE,
        "sub": username,
        "role": role or "user",
        "type": token_type,
        "iat": now,
        "nbf": now,
        "exp": now + ttl,
        "jti": secrets.token_urlsafe(16),
    }
    if session_id:
        payload["sid"] = session_id
    encoded_header = _b64encode(
        json.dumps(header, separators=(",", ":"), sort_keys=True).encode()
    )
    encoded_payload = _b64encode(
        json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()
    )
    signing_input = f"{encoded_header}.{encoded_payload}".encode("ascii")
    signature = hmac.new(_secret(settings), signing_input, hashlib.sha256).digest()
    return f"{encoded_header}.{encoded_payload}.{_b64encode(signature)}"


def decode_token(
    settings: dict[str, Any], token: str, *, expected_type: str = "access"
) -> dict[str, Any] | None:
    try:
        encoded_header, encoded_payload, encoded_signature = token.split(".")
        signing_input = f"{encoded_header}.{encoded_payload}".encode("ascii")
        expected = hmac.new(_secret(settings), signing_input, hashlib.sha256).digest()
        if not hmac.compare_digest(expected, _b64decode(encoded_signature)):
            return None
        header = json.loads(_b64decode(encoded_header))
        payload = json.loads(_b64decode(encoded_payload))
        now = int(time.time())
        if header.get("alg") != "HS256":
            return None
        if payload.get("iss") != _ISSUER or payload.get("aud") != _AUDIENCE:
            return None
        if payload.get("type") != expected_type:
            return None
        if int(payload.get("nbf", 0)) > now or int(payload.get("exp", 0)) <= now:
            return None
        if not isinstance(payload.get("sub"), str) or not payload["sub"]:
            return None
        return payload
    except (ValueError, TypeError, KeyError, RuntimeError):
        return None


def bearer_identity(request: Request) -> dict[str, Any] | None:
    authorization = request.headers.get("authorization", "")
    if not authorization.startswith("Bearer "):
        return None
    payload = decode_token(
        get_state(request).settings,
        authorization.removeprefix("Bearer ").strip(),
    )
    if not payload:
        return None
    return {
        "username": payload["sub"],
        "role": payload.get("role") or "user",
        "_session_id": payload.get("sid"),
        "via": "jwt",
    }


async def _credentials(request: Request) -> tuple[str, str]:
    content_type = request.headers.get("content-type", "")
    if "application/json" in content_type:
        try:
            body = await request.json()
        except json.JSONDecodeError:
            return "", ""
        return str(body.get("username") or ""), str(body.get("password") or "")
    form = await request.form()
    return str(form.get("username") or ""), str(form.get("password") or "")


def _token_pair(settings: dict[str, Any], user: dict[str, Any]) -> dict[str, Any]:
    username = str(user["username"])
    role = str(user.get("role") or "user")
    sid = user.get("_session_id")
    return {
        "access_token": encode_token(
            settings,
            username=username,
            role=role,
            token_type="access",
            ttl=_ACCESS_TTL,
            session_id=str(sid) if sid else None,
        ),
        "refresh_token": encode_token(
            settings,
            username=username,
            role=role,
            token_type="refresh",
            ttl=_REFRESH_TTL,
            session_id=str(sid) if sid else None,
        ),
        "token_type": "Bearer",
        "expires_in": _ACCESS_TTL,
    }


async def issue_token(request: Request):
    state = get_state(request)
    username, password = await _credentials(request)
    if not username or not password:
        return JSONResponse({"error": "invalid_credentials"}, status_code=401)
    service = state.services.get("user_service")
    user = service.authenticate(state.db_conn, username, password) if service else None
    if not user:
        return JSONResponse({"error": "invalid_credentials"}, status_code=401)
    try:
        return JSONResponse(_token_pair(state.settings, user))
    except RuntimeError as exc:
        return JSONResponse({"error": str(exc)}, status_code=503)


async def refresh_token(request: Request):
    try:
        body = await request.json()
    except json.JSONDecodeError:
        body = {}
    state = get_state(request)
    payload = decode_token(
        state.settings, str(body.get("refresh_token") or ""), expected_type="refresh"
    )
    if not payload:
        return JSONResponse({"error": "invalid_refresh_token"}, status_code=401)
    return JSONResponse(
        _token_pair(
            state.settings,
            {
                "username": payload["sub"],
                "role": payload.get("role") or "user",
                "_session_id": payload.get("sid"),
            },
        )
    )


routes = [
    Route("/auth/token", issue_token, methods=["POST"]),
    Route("/auth/refresh", refresh_token, methods=["POST"]),
]

