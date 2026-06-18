"""WebAuthn credential and challenge persistence."""

from __future__ import annotations

import base64
import logging
import secrets
import sqlite3
from datetime import datetime, timedelta, timezone

logger = logging.getLogger(__name__)

CHALLENGE_TTL_SECONDS = 300
PRF_SALT_ATTR = "webauthn_prf_salt"


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat() + "Z"


def _purge_expired_challenges(conn: sqlite3.Connection) -> None:
    cutoff = (datetime.now(timezone.utc) - timedelta(seconds=CHALLENGE_TTL_SECONDS)).isoformat() + "Z"
    try:
        conn.execute("DELETE FROM webauthn_challenges WHERE created_at < ?", (cutoff,))
    except sqlite3.Error:
        logger.debug("purge webauthn challenges failed", exc_info=True)


def store_challenge(
    conn: sqlite3.Connection,
    challenge: bytes,
    purpose: str,
    username: str | None = None,
) -> bool:
    if conn is None or not challenge or not purpose:
        return False
    _purge_expired_challenges(conn)
    challenge_b64 = base64.urlsafe_b64encode(challenge).decode("ascii").rstrip("=")
    try:
        with conn:
            conn.execute(
                "INSERT INTO webauthn_challenges (challenge, username, purpose, created_at) "
                "VALUES (?, ?, ?, ?)",
                (challenge_b64, username, purpose, _utcnow_iso()),
            )
        return True
    except sqlite3.Error as exc:
        logger.warning("store_challenge failed: %s", exc)
        return False


def consume_challenge(
    conn: sqlite3.Connection, challenge: bytes, purpose: str
) -> str | None:
    """Return username bound to challenge, or '' if none. None if invalid/expired."""
    if conn is None or not challenge:
        return None
    challenge_b64 = base64.urlsafe_b64encode(challenge).decode("ascii").rstrip("=")
    _purge_expired_challenges(conn)
    try:
        row = conn.execute(
            "SELECT username, created_at FROM webauthn_challenges "
            "WHERE challenge = ? AND purpose = ?",
            (challenge_b64, purpose),
        ).fetchone()
        if not row:
            return None
        created_at = datetime.fromisoformat(row[1].replace("Z", "+00:00"))
        if datetime.now(timezone.utc) - created_at > timedelta(seconds=CHALLENGE_TTL_SECONDS):
            conn.execute("DELETE FROM webauthn_challenges WHERE challenge = ?", (challenge_b64,))
            return None
        conn.execute("DELETE FROM webauthn_challenges WHERE challenge = ?", (challenge_b64,))
        conn.commit()
        return row[0] or ""
    except (sqlite3.Error, ValueError):
        return None


def list_credentials(conn: sqlite3.Connection, username: str) -> list[dict]:
    if conn is None or not username:
        return []
    try:
        rows = conn.execute(
            "SELECT id, credential_id, sign_count, transports, aaguid, prf_capable, "
            "nickname, created_at, last_used_at "
            "FROM webauthn_credentials WHERE username = ? ORDER BY created_at",
            (username,),
        ).fetchall()
        return [
            {
                "id": r[0],
                "credential_id": r[1],
                "sign_count": r[2],
                "transports": r[3],
                "aaguid": r[4],
                "prf_capable": bool(r[5]),
                "nickname": r[6],
                "created_at": r[7],
                "last_used_at": r[8],
            }
            for r in rows
        ]
    except sqlite3.Error:
        return []


def get_credential_by_id(conn: sqlite3.Connection, cred_db_id: int, username: str) -> dict | None:
    if conn is None:
        return None
    try:
        row = conn.execute(
            "SELECT id, username, credential_id, public_key, sign_count, transports, "
            "aaguid, prf_capable, nickname "
            "FROM webauthn_credentials WHERE id = ? AND username = ?",
            (cred_db_id, username),
        ).fetchone()
        if not row:
            return None
        return {
            "id": row[0],
            "username": row[1],
            "credential_id": row[2],
            "public_key": row[3],
            "sign_count": row[4],
            "transports": row[5],
            "aaguid": row[6],
            "prf_capable": bool(row[7]),
            "nickname": row[8],
        }
    except sqlite3.Error:
        return None


def get_credential_by_credential_id(conn: sqlite3.Connection, credential_id_b64: str) -> dict | None:
    if conn is None or not credential_id_b64:
        return None
    try:
        row = conn.execute(
            "SELECT id, username, credential_id, public_key, sign_count, transports, "
            "aaguid, prf_capable, nickname "
            "FROM webauthn_credentials WHERE credential_id = ?",
            (credential_id_b64,),
        ).fetchone()
        if not row:
            return None
        return {
            "id": row[0],
            "username": row[1],
            "credential_id": row[2],
            "public_key": row[3],
            "sign_count": row[4],
            "transports": row[5],
            "aaguid": row[6],
            "prf_capable": bool(row[7]),
            "nickname": row[8],
        }
    except sqlite3.Error:
        return None


def create_credential(
    conn: sqlite3.Connection,
    *,
    username: str,
    credential_id: str,
    public_key: bytes,
    sign_count: int,
    transports: str | None,
    aaguid: str | None,
    prf_capable: bool,
    nickname: str | None = None,
) -> bool:
    if conn is None or not username or not credential_id or not public_key:
        return False
    try:
        with conn:
            conn.execute(
                "INSERT INTO webauthn_credentials "
                "(username, credential_id, public_key, sign_count, transports, aaguid, "
                "prf_capable, nickname, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    username,
                    credential_id,
                    public_key,
                    sign_count,
                    transports,
                    aaguid,
                    1 if prf_capable else 0,
                    nickname,
                    _utcnow_iso(),
                ),
            )
        return True
    except sqlite3.Error as exc:
        logger.warning("create_credential failed: %s", exc)
        return False


def update_sign_count(conn: sqlite3.Connection, cred_db_id: int, sign_count: int) -> bool:
    if conn is None:
        return False
    try:
        with conn:
            conn.execute(
                "UPDATE webauthn_credentials SET sign_count = ?, last_used_at = ? WHERE id = ?",
                (sign_count, _utcnow_iso(), cred_db_id),
            )
        return True
    except sqlite3.Error:
        return False


def delete_credential(conn: sqlite3.Connection, cred_db_id: int, username: str) -> bool:
    if conn is None:
        return False
    try:
        with conn:
            cur = conn.execute(
                "DELETE FROM webauthn_credentials WHERE id = ? AND username = ?",
                (cred_db_id, username),
            )
            return cur.rowcount > 0
    except sqlite3.Error:
        return False


def credential_id_to_b64(credential_id: bytes) -> str:
    return base64.urlsafe_b64encode(credential_id).decode("ascii").rstrip("=")


def ensure_prf_salt(conn: sqlite3.Connection, username: str) -> str | None:
    from aird.db.user_attributes import get_user_attributes, set_user_attribute

    attrs = get_user_attributes(conn, username)
    existing = attrs.get(PRF_SALT_ATTR)
    if existing:
        return existing
    salt = base64.urlsafe_b64encode(secrets.token_bytes(32)).decode("ascii").rstrip("=")
    if set_user_attribute(conn, username, PRF_SALT_ATTR, salt):
        return salt
    return None


def get_prf_salt(conn: sqlite3.Connection, username: str) -> str | None:
    from aird.db.user_attributes import get_user_attributes

    return get_user_attributes(conn, username).get(PRF_SALT_ATTR)
