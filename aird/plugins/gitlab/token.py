"""Resolve the folder owner's GitLab PAT (self only). Never used for share viewers."""

from __future__ import annotations

import os
import stat
from pathlib import Path

from aird.core.user_storage import user_home_for_username

_TOKEN_NAMES = ("gitlab_token", "GITLAB_TOKEN")


def _secrets_dir(username: str) -> Path:
    home = user_home_for_username(username)
    path = Path(home) / ".aird" / "secrets"
    path.mkdir(parents=True, exist_ok=True)
    try:
        path.chmod(stat.S_IRWXU)
    except OSError:
        pass
    return path


def token_file_for(username: str) -> Path:
    return _secrets_dir(username) / "gitlab_token"


def save_user_token(username: str, token: str) -> None:
    path = token_file_for(username)
    path.write_text((token or "").strip(), encoding="utf-8")
    try:
        path.chmod(stat.S_IRUSR | stat.S_IWUSR)
    except OSError:
        pass


def delete_user_token(username: str) -> None:
    path = token_file_for(username)
    try:
        path.unlink()
    except OSError:
        pass


def _read_glab_token(host: str) -> str | None:
    candidates = []
    appdata = os.environ.get("APPDATA") or os.environ.get("XDG_CONFIG_HOME")
    if appdata:
        candidates.append(Path(appdata) / "glab-cli" / "config.yml")
    candidates.append(Path.home() / ".config" / "glab-cli" / "config.yml")
    host_key = (host or "gitlab.com").replace("https://", "").replace("http://", "").split("/")[0]
    for path in candidates:
        if not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        in_hosts = False
        in_host = False
        for raw in text.splitlines():
            line = raw.rstrip()
            stripped = line.strip()
            if stripped.startswith("hosts:"):
                in_hosts = True
                in_host = False
                continue
            if not in_hosts:
                continue
            if stripped.endswith(":") and not stripped.startswith("token"):
                name = stripped[:-1].strip().strip("'\"")
                in_host = name == host_key or name.endswith(host_key)
                continue
            if in_host and stripped.startswith("token:"):
                value = stripped.split(":", 1)[1].strip().strip("'\"")
                if value:
                    return value
    return None


def load_owner_token(username: str, host: str = "https://gitlab.com") -> str | None:
    path = token_file_for(username)
    if path.is_file():
        value = path.read_text(encoding="utf-8").strip()
        if value:
            return value
    for env_name in ("GITLAB_TOKEN", "GL_TOKEN", "GITLAB_PRIVATE_TOKEN"):
        value = (os.environ.get(env_name) or "").strip()
        if value:
            return value
    return _read_glab_token(host)


def token_configured(username: str, host: str = "https://gitlab.com") -> bool:
    return bool(load_owner_token(username, host))
