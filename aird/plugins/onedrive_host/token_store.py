"""Persist host OneDrive tokens under the user home directory."""

from __future__ import annotations

import json
import logging
import os
import stat
from pathlib import Path

logger = logging.getLogger(__name__)


def token_dir() -> Path:
    base = Path.home() / ".aird" / "onedrive"
    base.mkdir(parents=True, exist_ok=True)
    try:
        base.chmod(stat.S_IRWXU)
    except OSError:
        pass
    return base


def token_path() -> Path:
    return token_dir() / "token.json"


def load_file_token() -> dict | None:
    path = token_path()
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def save_file_token(payload: dict) -> None:
    path = token_path()
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    try:
        path.chmod(stat.S_IRUSR | stat.S_IWUSR)
    except OSError:
        pass


def clear_file_token() -> None:
    try:
        token_path().unlink(missing_ok=True)
    except OSError:
        pass


def file_token_configured() -> bool:
    data = load_file_token()
    return bool(data and (data.get("access_token") or data.get("refresh_token")))
