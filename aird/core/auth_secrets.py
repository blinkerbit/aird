"""Central auth secret resolution, verification, and secure persistence."""

from __future__ import annotations

import hashlib
import logging
import os
import secrets
import stat
from pathlib import Path

logger = logging.getLogger(__name__)

HASH_PREFIX = "sha256:"
_DEFAULT_TOKEN_BYTES = 48


def hash_auth_secret(plaintext: str) -> str:
    """Return a stored representation safe to keep in config files."""
    digest = hashlib.sha256(plaintext.strip().encode("utf-8")).hexdigest()
    return f"{HASH_PREFIX}{digest}"


def verify_auth_secret(provided: str | None, stored: str | None) -> bool:
    """Constant-time verify of a login/Bearer token against plain or hashed storage."""
    if not provided or not stored:
        return False
    supplied = provided.strip()
    if not supplied:
        return False
    if stored.startswith(HASH_PREFIX):
        expected = stored[len(HASH_PREFIX) :]
        if not expected:
            return False
        got = hashlib.sha256(supplied.encode("utf-8")).hexdigest()
        return secrets.compare_digest(got, expected)
    return secrets.compare_digest(supplied, stored.strip())


def secrets_dir_for_root(root_dir: str) -> Path:
    """Directory for machine-local generated secrets (mode 0700)."""
    path = Path(root_dir).resolve() / ".aird" / "secrets"
    path.mkdir(parents=True, exist_ok=True)
    try:
        path.chmod(stat.S_IRWXU)
    except OSError:
        pass
    return path


def _write_secret_file(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value, encoding="utf-8")
    try:
        path.chmod(stat.S_IRUSR | stat.S_IWUSR)
    except OSError:
        pass


def load_or_create_secret_file(secrets_dir: Path, filename: str) -> tuple[str, bool]:
    """Read an existing secret file or create one.

    Returns (value, from_persisted_file). *from_persisted_file* is False only when
    a new secret file was created in this call.
    """
    path = secrets_dir / filename
    if path.is_file():
        return path.read_text(encoding="utf-8").strip(), True
    value = secrets.token_urlsafe(_DEFAULT_TOKEN_BYTES)
    _write_secret_file(path, value)
    return value, False


def normalize_stored_secret(value: str | None) -> str | None:
    """Accept plain text, sha256:hex, or bare hex digest from config."""
    if not value or not str(value).strip():
        return None
    raw = str(value).strip()
    if raw.startswith(HASH_PREFIX):
        return raw
    if len(raw) == 64 and all(c in "0123456789abcdefABCDEF" for c in raw):
        return f"{HASH_PREFIX}{raw.lower()}"
    return raw


def resolve_auth_secret(
    *,
    cli_value: str | None,
    config_value: str | None,
    env_value: str | None,
    secrets_dir: Path,
    secret_filename: str,
    allow_auto_generate: bool,
) -> tuple[str | None, bool]:
    """Resolve an auth secret.

    Precedence: CLI > env > config (plain or hash) > secrets file (auto modes only).

    Returns (stored_value, explicit). *stored_value* may be plain or ``sha256:`` hash.
    *explicit* is True when supplied by operator (CLI/env/config).
    """
    for candidate in (cli_value, env_value, config_value):
        normalized = normalize_stored_secret(candidate)
        if normalized:
            return normalized, True

    if not allow_auto_generate:
        return None, False

    file_value, from_persisted = load_or_create_secret_file(
        secrets_dir, secret_filename
    )
    if file_value:
        return file_value, from_persisted

    return None, False


def resolve_cookie_secret(root_dir: str) -> tuple[str, bool]:
    """Resolve signing key for session cookies."""
    env_val = os.environ.get("AIRD_COOKIE_SECRET", "").strip()
    if env_val:
        return env_val, True
    secrets_dir = secrets_dir_for_root(root_dir)
    value, created = load_or_create_secret_file(secrets_dir, "cookie_secret")
    return value, not created


def describe_ephemeral_secret(
    name: str, secrets_dir: Path, filename: str, *, created: bool
) -> str:
    """Human-readable notice for operator; never log the secret itself."""
    path = secrets_dir / filename
    if created:
        return (
            f"{name} was generated and saved to {path} (mode 600). "
            f"Back up this file or set an explicit env var."
        )
    return f"{name} loaded from {path}."
