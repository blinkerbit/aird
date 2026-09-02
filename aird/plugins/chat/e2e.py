"""E2E helpers: public identity keys, conversation wraps, ciphertext validation.

Private keys never leave the browser. The server only stores public JWKs,
AES-key wraps (encrypted to each member's public key), and message ciphertext.
"""

from __future__ import annotations

import base64
import json
import logging
import re
import sqlite3
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

ENCRYPTED_PREVIEW = "Encrypted message"
_MAX_JWK_CHARS = 2_000
_MAX_WRAP_CHARS = 8_000
_MAX_CT_CHARS = 48_000
_B64_RE = re.compile(r"^[A-Za-z0-9+/_-]+=*$")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def is_e2e_meta(meta: dict | None) -> bool:
    e2e = (meta or {}).get("e2e")
    return isinstance(e2e, dict) and e2e.get("v") == 1 and bool(e2e.get("ct")) and bool(e2e.get("iv"))


def is_e2e_message(msg: dict | None) -> bool:
    return is_e2e_meta((msg or {}).get("metadata"))


def _b64_ok(value: object, *, min_len: int, max_len: int) -> bool:
    if not isinstance(value, str) or not (min_len <= len(value) <= max_len):
        return False
    if not _B64_RE.match(value):
        return False
    pad = "=" * ((4 - len(value) % 4) % 4)
    try:
        base64.urlsafe_b64decode(value.replace("+", "-").replace("/", "_") + pad)
    except (ValueError, TypeError):
        return False
    return True


def validate_public_jwk(raw) -> dict:
    if isinstance(raw, str):
        if len(raw) > _MAX_JWK_CHARS:
            raise ValueError("Public key too large")
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError("Invalid public key") from exc
    if not isinstance(raw, dict):
        raise ValueError("Invalid public key")
    if raw.get("kty") != "EC" or raw.get("crv") != "P-256":
        raise ValueError("Public key must be EC P-256")
    if "d" in raw:
        raise ValueError("Private key material is not allowed")
    x, y = raw.get("x"), raw.get("y")
    if not _b64_ok(x, min_len=20, max_len=88) or not _b64_ok(y, min_len=20, max_len=88):
        raise ValueError("Invalid public key coordinates")
    return {"kty": "EC", "crv": "P-256", "x": str(x), "y": str(y)}


def parse_e2e_payload(raw) -> dict:
    if not isinstance(raw, dict):
        raise ValueError("Invalid encrypted payload")
    iv, ct = raw.get("iv"), raw.get("ct")
    if raw.get("v") != 1 or not _b64_ok(iv, min_len=8, max_len=64) or not _b64_ok(ct, min_len=8, max_len=_MAX_CT_CHARS):
        raise ValueError("Invalid encrypted payload")
    return {"v": 1, "iv": str(iv), "ct": str(ct)}


def parse_wrap(raw) -> dict:
    if not isinstance(raw, dict):
        raise ValueError("Invalid wrap")
    blob = json.dumps(raw, separators=(",", ":"))
    if len(blob) > _MAX_WRAP_CHARS:
        raise ValueError("Wrap too large")
    epk = validate_public_jwk(raw.get("epk"))
    iv, ct, kid = raw.get("iv"), raw.get("ct"), raw.get("kid")
    if raw.get("v") != 1 or not _b64_ok(iv, min_len=8, max_len=64) or not _b64_ok(ct, min_len=8, max_len=1024):
        raise ValueError("Invalid wrap")
    if not isinstance(kid, str) or not (8 <= len(kid) <= 128) or not _B64_RE.match(kid):
        raise ValueError("Invalid wrap")
    return {"v": 1, "kid": kid, "epk": epk, "iv": str(iv), "ct": str(ct)}


def ensure_identity_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS chat_e2e_keys (
            username TEXT PRIMARY KEY,
            public_jwk TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )


def put_identity_key(conn: sqlite3.Connection, username: str, public_jwk: dict) -> dict:
    ensure_identity_table(conn)
    jwk = validate_public_jwk(public_jwk)
    conn.execute(
        """
        INSERT INTO chat_e2e_keys (username, public_jwk, updated_at)
        VALUES (?, ?, ?)
        ON CONFLICT(username) DO UPDATE SET
            public_jwk = excluded.public_jwk,
            updated_at = excluded.updated_at
        """,
        (username, json.dumps(jwk, separators=(",", ":")), _now_iso()),
    )
    conn.commit()
    return jwk


def get_identity_keys(conn: sqlite3.Connection, usernames: list[str]) -> dict[str, dict]:
    ensure_identity_table(conn)
    names = [str(n) for n in usernames if n]
    if not names:
        return {}
    q = ",".join("?" * len(names))
    rows = conn.execute(
        f"SELECT username, public_jwk FROM chat_e2e_keys WHERE username IN ({q})",
        names,
    ).fetchall()
    out: dict[str, dict] = {}
    for username, raw in rows:
        try:
            out[str(username)] = validate_public_jwk(raw)
        except ValueError:
            continue
    return out


def metadata_from_e2e_request(body: dict) -> dict:
    e2e = parse_e2e_payload(body.get("e2e"))
    mentions = body.get("mentions") if isinstance(body.get("mentions"), list) else []
    clean: list[str] = []
    for raw in mentions[:50]:
        name = str(raw or "").strip()
        if name and name not in clean:
            clean.append(name)
    return {"e2e": e2e, "mentions": clean}
