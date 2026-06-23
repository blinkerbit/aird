"""Encrypt/decrypt secrets at rest (network share passwords, etc.)."""

from __future__ import annotations

import base64
import hashlib
import logging
import os
import threading

logger = logging.getLogger(__name__)

_PREFIX = "enc:v1:"
_fernet = None
_fernet_unavailable_logged = False
_fernet_lock = threading.Lock()


def _reset_fernet_cache() -> None:
    global _fernet, _fernet_unavailable_logged
    _fernet = None
    _fernet_unavailable_logged = False


def _get_fernet():
    global _fernet, _fernet_unavailable_logged
    with _fernet_lock:
        if _fernet is not None:
            return _fernet
        key_material = (
            os.environ.get("AIRD_SECRETS_KEY", "").strip()
            or os.environ.get("AIRD_COOKIE_SECRET", "").strip()
        )
        if not key_material:
            if not _fernet_unavailable_logged:
                logger.debug(
                    "AIRD_SECRETS_KEY / AIRD_COOKIE_SECRET unset; secrets stored as plaintext"
                )
                _fernet_unavailable_logged = True
            return None
        try:
            from cryptography.fernet import Fernet

            derived = base64.urlsafe_b64encode(
                hashlib.sha256(key_material.encode("utf-8")).digest()
            )
            _fernet = Fernet(derived)
            return _fernet
        except Exception:
            logger.warning("Could not initialize secret encryption", exc_info=True)
            return None


def encrypt_secret(plaintext: str) -> str:
    if not plaintext or plaintext.startswith(_PREFIX):
        return plaintext
    fernet = _get_fernet()
    if fernet is None:
        return plaintext
    return _PREFIX + fernet.encrypt(plaintext.encode("utf-8")).decode("ascii")


def decrypt_secret(stored: str) -> str:
    if not stored or not stored.startswith(_PREFIX):
        return stored
    fernet = _get_fernet()
    if fernet is None:
        logger.warning("Encrypted secret in DB but no AIRD_SECRETS_KEY configured")
        return stored
    try:
        token = stored[len(_PREFIX) :].encode("ascii")
        return fernet.decrypt(token).decode("utf-8")
    except Exception:
        logger.warning("Failed to decrypt stored secret", exc_info=True)
        return stored
