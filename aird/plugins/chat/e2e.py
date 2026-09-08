"""E2E helpers: identity keys, conversation wraps, ciphertext validation.

Identity private keys and conversation AES keys are stored in the user's
``.aird-chats`` folder so devices can restore them. Message bodies stay
ciphertext in the mailbox.
"""

from __future__ import annotations

import base64
import json
import logging
import os
import re
import sqlite3
import threading
import time
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

ENCRYPTED_PREVIEW = "Encrypted message"
IDENTITY_FILENAME = "e2e-identity.json"
CONV_KEY_FILENAME = "e2e-conv.json"
_MAX_JWK_CHARS = 2_000
_MAX_WRAP_CHARS = 8_000
_MAX_CT_CHARS = 48_000
_CONV_ID_RE = re.compile(r"^[0-9a-fA-F-]{36}$")
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


def _chat_account_dir(username: str) -> str:
    from aird.plugins.chat.mailbox import mailbox_path_for

    return os.path.dirname(mailbox_path_for(username))


_WRITE_LOCKS: dict[str, threading.Lock] = {}
_WRITE_LOCKS_GUARD = threading.Lock()


def _path_lock(path: str) -> threading.Lock:
    with _WRITE_LOCKS_GUARD:
        lock = _WRITE_LOCKS.get(path)
        if lock is None:
            lock = threading.Lock()
            _WRITE_LOCKS[path] = lock
        return lock


def _atomic_write_json(path: str, payload: dict) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    existing = _read_json(path)
    if existing == payload:
        return
    tmp = f"{path}.{os.getpid()}.{threading.get_ident()}.{time.time_ns()}.tmp"
    with _path_lock(os.path.normcase(os.path.abspath(path))):
        existing = _read_json(path)
        if existing == payload:
            return
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, separators=(",", ":"))
            fh.flush()
            os.fsync(fh.fileno())
        last_err: OSError | None = None
        for delay in (0.02, 0.05, 0.1, 0.2, 0.4):
            try:
                os.replace(tmp, path)
                return
            except PermissionError as exc:
                last_err = exc
                time.sleep(delay)
        try:
            os.replace(tmp, path)
        except OSError:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            if last_err is not None:
                raise last_err
            raise


def _read_json(path: str) -> dict | None:
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, json.JSONDecodeError, TypeError):
        return None
    return data if isinstance(data, dict) else None


def parse_identity_backup(raw, username: str) -> dict:
    if not isinstance(raw, dict) or raw.get("v") != 1 or raw.get("kind") != "aird-chat-e2e-identity":
        raise ValueError("Invalid key backup")
    owner = str(raw.get("username") or "").strip()
    if owner != username:
        raise ValueError("Backup belongs to another user")
    pub = validate_public_jwk(raw.get("publicJwk"))
    priv_raw = raw.get("privateJwk")
    if not isinstance(priv_raw, dict) or "d" not in priv_raw:
        raise ValueError("Backup missing private key")
    priv_pub = validate_public_jwk(
        {k: priv_raw[k] for k in ("kty", "crv", "x", "y") if k in priv_raw}
    )
    d = priv_raw.get("d")
    if not _b64_ok(d, min_len=20, max_len=88):
        raise ValueError("Invalid private key")
    kid = raw.get("kid")
    if not isinstance(kid, str) or not (8 <= len(kid) <= 128) or not _B64_RE.match(kid):
        raise ValueError("Invalid key id")
    exported = str(raw.get("exported_at") or _now_iso())[:48]
    return {
        "v": 1,
        "kind": "aird-chat-e2e-identity",
        "username": username,
        "exported_at": exported,
        "publicJwk": pub,
        "privateJwk": {**priv_pub, "d": str(d)},
        "kid": kid,
    }


def identity_backup_path(username: str) -> str:
    return os.path.join(_chat_account_dir(username), IDENTITY_FILENAME)


def load_identity_backup(username: str) -> dict | None:
    data = _read_json(identity_backup_path(username))
    if not data:
        return None
    try:
        return parse_identity_backup(data, username)
    except ValueError:
        return None


def save_identity_backup(username: str, bundle: dict) -> dict:
    clean = parse_identity_backup(bundle, username)
    _atomic_write_json(identity_backup_path(username), clean)
    return clean


def parse_conv_key(raw, conversation_id: str) -> dict:
    if not isinstance(raw, dict) or raw.get("v") != 1:
        raise ValueError("Invalid conversation key")
    cid = str(raw.get("conversation_id") or conversation_id).strip()
    if cid != conversation_id:
        raise ValueError("Conversation key mismatch")
    if not _CONV_ID_RE.match(conversation_id):
        raise ValueError("Invalid conversation id")
    key = raw.get("key") or raw.get("raw_b64")
    if not _b64_ok(key, min_len=32, max_len=88):
        raise ValueError("Invalid conversation key")
    return {
        "v": 1,
        "kind": "aird-chat-e2e-conv",
        "conversation_id": conversation_id,
        "key": str(key),
    }


def conv_key_path(username: str, conversation_id: str) -> str:
    if not _CONV_ID_RE.match(conversation_id or ""):
        raise ValueError("Invalid conversation id")
    return os.path.join(_chat_account_dir(username), "c", conversation_id, CONV_KEY_FILENAME)


def load_conv_key(username: str, conversation_id: str) -> dict | None:
    try:
        data = _read_json(conv_key_path(username, conversation_id))
    except ValueError:
        return None
    if not data:
        return None
    try:
        return parse_conv_key(data, conversation_id)
    except ValueError:
        return None


def save_conv_key(username: str, conversation_id: str, bundle: dict) -> dict:
    clean = parse_conv_key(bundle, conversation_id)
    current = load_conv_key(username, conversation_id)
    if current and current.get("key") == clean["key"]:
        return current
    _atomic_write_json(conv_key_path(username, conversation_id), clean)
    return clean


def share_conv_key(usernames: list[str], conversation_id: str, bundle: dict) -> dict:
    clean = parse_conv_key(bundle, conversation_id)
    names = [str(n).strip() for n in usernames if str(n or "").strip()]
    for name in names:
        save_conv_key(name, conversation_id, clean)
    return clean


def bind_conv_key(usernames: list[str], conversation_id: str, bundle: dict) -> dict:
    """Keep the first stored conversation key; fan it out instead of replacing it."""
    names = [str(n).strip() for n in usernames if str(n or "").strip()]
    existing = None
    for name in names:
        existing = load_conv_key(name, conversation_id)
        if existing:
            break
    if existing:
        return share_conv_key(names, conversation_id, existing)
    return share_conv_key(names, conversation_id, bundle)


def adopt_conv_key(username: str, conversation_id: str, members: list[str]) -> dict | None:
    """Copy a conversation key from any member's Aird folder to every member."""
    names = [str(n).strip() for n in members if str(n or "").strip()]
    if username and username not in names:
        names.append(username)
    found = None
    for name in names:
        found = load_conv_key(name, conversation_id)
        if found:
            break
    if not found:
        return None
    for name in names:
        have = load_conv_key(name, conversation_id)
        if not have or have.get("key") != found["key"]:
            return share_conv_key(names, conversation_id, found)
    return found


_E2E_INFO = b"aird-chat-e2e-v1"


def _b64_decode(value: str) -> bytes:
    s = str(value or "").strip()
    if not s:
        raise ValueError("empty b64")
    pad = "=" * ((4 - len(s) % 4) % 4)
    return base64.urlsafe_b64decode(s.replace("+", "-").replace("/", "_") + pad)


def _b64_std(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def _ec_private_from_jwk(jwk: dict):
    from cryptography.hazmat.primitives.asymmetric import ec

    d = int.from_bytes(_b64_decode(jwk["d"]), "big")
    x = int.from_bytes(_b64_decode(jwk["x"]), "big")
    y = int.from_bytes(_b64_decode(jwk["y"]), "big")
    pub = ec.EllipticCurvePublicNumbers(x, y, ec.SECP256R1())
    return ec.EllipticCurvePrivateNumbers(d, pub).private_key()


def _ec_public_from_jwk(jwk: dict):
    from cryptography.hazmat.primitives.asymmetric import ec

    x = int.from_bytes(_b64_decode(jwk["x"]), "big")
    y = int.from_bytes(_b64_decode(jwk["y"]), "big")
    return ec.EllipticCurvePublicNumbers(x, y, ec.SECP256R1()).public_key()


def unwrap_conv_key_bytes(private_jwk: dict, wrap: dict, conversation_id: str) -> bytes:
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    from cryptography.hazmat.primitives.kdf.hkdf import HKDF

    priv = _ec_private_from_jwk(private_jwk)
    eph = _ec_public_from_jwk(wrap["epk"])
    shared = priv.exchange(ec.ECDH(), eph)
    aes_key = HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=str(conversation_id).encode("utf-8"),
        info=_E2E_INFO,
    ).derive(shared)
    pt = AESGCM(aes_key).decrypt(_b64_decode(wrap["iv"]), _b64_decode(wrap["ct"]), None)
    if len(pt) != 32:
        raise ValueError("Bad conversation key length")
    return bytes(pt)


def recover_conv_key(conversation_id: str, members: list[str], wraps: dict) -> dict | None:
    """Unwrap a stored wrap using a member's Aird identity file and share the raw key."""
    if not isinstance(wraps, dict) or not wraps:
        return None
    names = [str(n).strip() for n in members if str(n or "").strip()]
    wrap_items = [(str(n), w) for n, w in wraps.items() if n and isinstance(w, dict)]
    backups = []
    for name in names:
        backup = load_identity_backup(name)
        if backup:
            backups.append((name, backup))
    for owner, backup in backups:
        for wrap_name, wrap in wrap_items:
            try:
                raw = unwrap_conv_key_bytes(backup["privateJwk"], wrap, conversation_id)
            except Exception:
                logger.debug(
                    "e2e recover unwrap failed for %s wrap=%s", owner, wrap_name, exc_info=True
                )
                continue
            bundle = {
                "v": 1,
                "kind": "aird-chat-e2e-conv",
                "conversation_id": conversation_id,
                "key": _b64_std(raw),
            }
            return share_conv_key(names, conversation_id, bundle)
    return None
