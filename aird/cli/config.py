"""CLI configuration and session file paths."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

CONFIG_DIR_ENV = "AIRD_CLI_CONFIG_DIR"
DEFAULT_DIR_NAME = "aird"


def config_dir() -> Path:
    override = os.environ.get(CONFIG_DIR_ENV, "").strip()
    if override:
        return Path(override).expanduser()
    if sys.platform == "win32":
        base = os.environ.get("APPDATA") or str(Path.home())
        return Path(base) / DEFAULT_DIR_NAME
    xdg = os.environ.get("XDG_CONFIG_HOME")
    if xdg:
        return Path(xdg) / DEFAULT_DIR_NAME
    return Path.home() / ".config" / DEFAULT_DIR_NAME


def config_path() -> Path:
    return config_dir() / "config.json"


def session_path() -> Path:
    return config_dir() / "session.json"


def ensure_config_dir() -> Path:
    d = config_dir()
    d.mkdir(parents=True, exist_ok=True)
    return d


def load_config() -> dict[str, Any]:
    path = config_path()
    if not path.is_file():
        return {}
    try:
        with path.open(encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def save_config(data: dict[str, Any]) -> None:
    ensure_config_dir()
    with config_path().open("w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
        f.write("\n")
    try:
        os.chmod(config_path(), 0o600)
    except OSError:
        pass


def get_server_url(cfg: dict[str, Any] | None = None) -> str | None:
    cfg = cfg if cfg is not None else load_config()
    url = (
        os.environ.get("AIRD_SERVER", "").strip()
        or str(cfg.get("server") or "").strip()
    )
    return url.rstrip("/") if url else None


def get_authelia_url(cfg: dict[str, Any] | None = None) -> str | None:
    cfg = cfg if cfg is not None else load_config()
    url = (
        os.environ.get("AIRD_AUTHELIA_URL", "").strip()
        or str(cfg.get("authelia_url") or "").strip()
    )
    return url.rstrip("/") if url else None


def get_parallel_jobs(cfg: dict[str, Any] | None = None, default: int = 2) -> int:
    cfg = cfg if cfg is not None else load_config()
    raw = (
        os.environ.get("AIRD_PARALLEL_JOBS", "").strip()
        or cfg.get("parallel_uploads")
        or cfg.get("parallel_downloads")
    )
    try:
        n = int(raw)
        return max(1, n) if n > 0 else default
    except (TypeError, ValueError):
        return default
