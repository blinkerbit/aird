"""HTTP session wrapper for Aird backend APIs."""

from __future__ import annotations

import json
import logging
import os
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Callable, Iterator
from urllib.parse import quote, urljoin

import requests

from aird.cli.config import ensure_config_dir, get_authelia_url, get_server_url, session_path

logger = logging.getLogger(__name__)

XSRF_COOKIE = "_xsrf"


def _remote_url(base_path: str, remote_path: str) -> str:
    remote_path = remote_path.strip("/")
    if not remote_path:
        return base_path.rstrip("/") + "/"
    segments = remote_path.split("/")
    return base_path.rstrip("/") + "/" + "/".join(quote(s, safe="") for s in segments)


class AirdAuthError(RuntimeError):
    pass


class AirdAPIError(RuntimeError):
    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


def _cookie_to_dict(c: requests.cookies.Cookie) -> dict[str, Any]:
    return {
        "name": c.name,
        "value": c.value,
        "domain": c.domain,
        "path": c.path,
        "secure": bool(c.secure),
    }


def _save_cookies(session: requests.Session) -> None:
    ensure_config_dir()
    path = session_path()
    data = {
        "cookies": [_cookie_to_dict(c) for c in session.cookies],
        "bearer_token": session.headers.get("Authorization", "").removeprefix("Bearer ").strip()
        or None,
    }
    with path.open("w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
        f.write("\n")
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def _load_cookies(session: requests.Session) -> bool:
    path = session_path()
    if not path.is_file():
        return False
    try:
        with path.open(encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return False
    if not isinstance(data, dict):
        return False
    for item in data.get("cookies") or []:
        if not isinstance(item, dict) or not item.get("name"):
            continue
        session.cookies.set(
            item["name"],
            item.get("value", ""),
            domain=item.get("domain") or "",
            path=item.get("path") or "/",
        )
    token = data.get("bearer_token")
    if token:
        session.headers["Authorization"] = f"Bearer {token}"
    return bool(data.get("cookies") or token)


class AirdClient:
    def __init__(self, server_url: str | None = None, *, _reuse: "AirdClient | None" = None):
        if _reuse is not None:
            self.server = _reuse.server
            self.http = requests.Session()
            self.http.headers.update(_reuse.http.headers)
            self.http.cookies.update(_reuse.http.cookies)
            self._xsrf = _reuse._xsrf
            return
        self.server = (server_url or get_server_url() or "").rstrip("/")
        if not self.server:
            raise AirdAuthError(
                "Server URL not set. Run: aird-cli config set server https://your-host"
            )
        self.http = requests.Session()
        self.http.headers.setdefault("User-Agent", "aird-cli/0.1")
        self._xsrf: str | None = None
        _load_cookies(self.http)

    def clone(self) -> "AirdClient":
        """Thread-safe copy of this client (separate requests.Session, same auth)."""
        return AirdClient(_reuse=self)

    def set_bearer_token(self, token: str) -> None:
        token = token.strip()
        if token:
            self.http.headers["Authorization"] = f"Bearer {token}"
        elif "Authorization" in self.http.headers:
            del self.http.headers["Authorization"]

    def save(self) -> None:
        _save_cookies(self.http)

    def clear_session(self) -> None:
        self.http.cookies.clear()
        self.http.headers.pop("Authorization", None)
        self._xsrf = None
        path = session_path()
        if path.is_file():
            path.unlink()

    def _url(self, path: str) -> str:
        if not path.startswith("/"):
            path = "/" + path
        return urljoin(self.server + "/", path.lstrip("/"))

    def refresh_xsrf(self) -> str:
        r = self.http.get(self._url("/login"), timeout=60, allow_redirects=True)
        xsrf = self.http.cookies.get(XSRF_COOKIE) or ""
        if not xsrf:
            m = re.search(r'name="_xsrf"\s+value="([^"]+)"', r.text)
            if m:
                xsrf = m.group(1)
        if xsrf:
            self._xsrf = xsrf
        return xsrf

    def _xsrf_header(self) -> dict[str, str]:
        if not self._xsrf:
            self.refresh_xsrf()
        if self._xsrf:
            return {"X-XSRFToken": self._xsrf}
        return {}

    def login_password(
        self,
        username: str,
        password: str,
        *,
        token: str | None = None,
    ) -> None:
        xsrf = self.refresh_xsrf()
        data: dict[str, str] = {}
        if token:
            data["token"] = token
        else:
            data["username"] = username
            data["password"] = password
        if xsrf:
            data["_xsrf"] = xsrf
        r = self.http.post(
            self._url("/login"),
            data=data,
            headers=self._xsrf_header(),
            timeout=60,
            allow_redirects=False,
        )
        if r.status_code not in (302, 303):
            raise AirdAuthError("Login failed — check username, password, or token")
        self.save()

    def check_auth(self) -> dict[str, Any]:
        r = self.http.get(self._url("/api/files/"), timeout=60)
        if r.status_code == 401:
            raise AirdAuthError("Not authenticated")
        if r.status_code >= 400:
            raise AirdAPIError(f"Auth check failed (HTTP {r.status_code})", r.status_code)
        try:
            return r.json()
        except ValueError:
            return {}

    def ensure_auth(self) -> None:
        try:
            self.check_auth()
            return
        except (AirdAuthError, AirdAPIError):
            pass
        raise AirdAuthError("Session expired. Run: aird-cli login")

    def list_dir(self, remote_path: str = "") -> list[dict[str, Any]]:
        self.ensure_auth()
        url = self._url(_remote_url("/api/files", remote_path))
        r = self.http.get(url, timeout=60)
        if r.status_code == 403:
            raise AirdAPIError("Access denied", 403)
        if r.status_code == 404:
            raise AirdAPIError("Path not found", 404)
        if r.status_code >= 400:
            raise AirdAPIError(f"List failed (HTTP {r.status_code})", r.status_code)
        payload = r.json()
        return list(payload.get("files") or [])

    def iter_tree(self, remote_dir: str = "") -> Iterator[tuple[str, int]]:
        """Yield (remote_file_path, size_bytes) for all files under *remote_dir*."""
        remote_dir = remote_dir.strip("/")
        entries = self.list_dir(remote_dir)
        for entry in entries:
            name = entry.get("name") or ""
            if not name:
                continue
            child = f"{remote_dir}/{name}" if remote_dir else name
            if entry.get("is_dir"):
                yield from self.iter_tree(child)
            else:
                size = int(entry.get("size_bytes") or entry.get("size") or 0)
                yield child, size

    def download_file(self, remote_path: str, local_path: Path) -> None:
        self.ensure_auth()
        url = self._url(_remote_url("/files", remote_path)) + "?download=1"
        local_path.parent.mkdir(parents=True, exist_ok=True)
        with self.http.get(url, stream=True, timeout=300) as r:
            if r.status_code >= 400:
                raise AirdAPIError(
                    f"Download failed for {remote_path} (HTTP {r.status_code})",
                    r.status_code,
                )
            with local_path.open("wb") as f:
                for chunk in r.iter_content(chunk_size=1024 * 1024):
                    if chunk:
                        f.write(chunk)

    def download_tree(
        self,
        remote_dir: str,
        local_dir: Path,
        *,
        workers: int = 2,
        on_progress: Callable[[str], None] | None = None,
    ) -> int:
        files = list(self.iter_tree(remote_dir))
        if not files:
            return 0
        remote_prefix = remote_dir.strip("/")

        def _job(item: tuple[str, int]) -> str:
            remote_path, _ = item
            if remote_prefix:
                rel = remote_path[len(remote_prefix) :].lstrip("/")
            else:
                rel = remote_path
            dest = local_dir / rel
            self.clone().download_file(remote_path, dest)
            return remote_path

        count = 0
        with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
            futures = [pool.submit(_job, f) for f in files]
            for fut in as_completed(futures):
                path = fut.result()
                count += 1
                if on_progress:
                    on_progress(path)
        return count

    def upload_file(self, local_path: Path, remote_dir: str = "") -> None:
        self.ensure_auth()
        if not local_path.is_file():
            raise FileNotFoundError(local_path)
        remote_dir = remote_dir.strip("/")
        filename = local_path.name
        r = self.http.post(
            self._url("/upload"),
            params={"upload_dir": remote_dir, "upload_filename": filename},
            data=local_path.read_bytes(),
            headers={
                **self._xsrf_header(),
                "Content-Type": "application/octet-stream",
            },
            timeout=600,
        )
        if r.status_code >= 400:
            raise AirdAPIError(
                f"Upload failed for {filename} (HTTP {r.status_code})",
                r.status_code,
            )

    def upload_tree(
        self,
        local_dir: Path,
        remote_dir: str = "",
        *,
        workers: int = 2,
        on_progress: Callable[[str], None] | None = None,
    ) -> int:
        if not local_dir.is_dir():
            raise NotADirectoryError(local_dir)
        local_dir = local_dir.resolve()
        files: list[tuple[Path, str]] = []

        def walk(base: Path, rel_remote: str) -> None:
            for child in sorted(base.iterdir()):
                rel = f"{rel_remote}/{child.name}" if rel_remote else child.name
                if child.is_dir():
                    walk(child, rel)
                elif child.is_file():
                    remote_parent = (
                        f"{remote_dir}/{rel_remote}".strip("/") if rel_remote else remote_dir
                    )
                    files.append((child, remote_parent.strip("/")))

        walk(local_dir, "")
        if not files:
            return 0

        count = 0
        def _upload_job(lp: Path, rd: str) -> Path:
            self.clone().upload_file(lp, rd)
            return lp

        with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
            futures = {pool.submit(_upload_job, lp, rd): (lp, rd) for lp, rd in files}
            for fut in as_completed(futures):
                lp, _ = futures[fut]
                fut.result()
                count += 1
                if on_progress:
                    on_progress(str(lp))
        return count

    def list_shares(self) -> dict[str, Any]:
        self.ensure_auth()
        r = self.http.get(self._url("/share/list"), timeout=60)
        if r.status_code >= 400:
            raise AirdAPIError(f"Share list failed (HTTP {r.status_code})", r.status_code)
        return r.json()

    def find_share(self, share_id: str) -> tuple[dict[str, Any] | None, bool]:
        """Return (share_dict, is_mine). *is_mine* True when you created the share."""
        data = self.list_shares()
        mine = data.get("shares") or {}
        if isinstance(mine, dict) and share_id in mine:
            return mine[share_id], True
        for s in data.get("shared_with_me") or []:
            if isinstance(s, dict) and s.get("id") == share_id:
                return s, False
        return None, False

    def expand_paths_to_files(self, paths: list[str]) -> list[str]:
        """Expand share path entries (files or folders) to file paths in your tree."""
        files: list[str] = []
        seen: set[str] = set()
        for raw in paths:
            p = (raw or "").strip("/")
            if not p:
                continue
            try:
                self.list_dir(p)
                for rel, _ in self.iter_tree(p):
                    if rel not in seen:
                        seen.add(rel)
                        files.append(rel)
            except AirdAPIError as exc:
                if exc.status == 404:
                    if p not in seen:
                        seen.add(p)
                        files.append(p)
                else:
                    raise
        return files

    def verify_share(self, share_id: str, token: str) -> None:
        xsrf = self.refresh_xsrf()
        r = self.http.post(
            self._url(f"/shared/{quote(share_id)}/verify"),
            json={"token": token},
            headers=self._xsrf_header(),
            timeout=60,
        )
        if r.status_code >= 400:
            raise AirdAPIError("Share token verification failed", r.status_code)
        self.save()

    def share_file_paths(self, share_id: str) -> list[str]:
        self.ensure_auth()
        r = self.http.get(self._url(f"/shared/{quote(share_id)}"), timeout=60)
        if r.status_code >= 400:
            raise AirdAPIError(f"Share not found (HTTP {r.status_code})", r.status_code)
        m = re.search(
            r'<script[^>]+id="files-json"[^>]*>(.*?)</script>',
            r.text,
            re.DOTALL,
        )
        if not m:
            return []
        try:
            data = json.loads(m.group(1).strip())
        except json.JSONDecodeError:
            return []
        if isinstance(data, list):
            return [str(p) for p in data]
        return []

    def download_share_file(
        self, share_id: str, file_path: str, local_path: Path
    ) -> None:
        self.ensure_auth()
        enc = quote(file_path, safe="/")
        url = self._url(f"/shared/{quote(share_id)}/file/{enc}?download=1")
        local_path.parent.mkdir(parents=True, exist_ok=True)
        with self.http.get(url, stream=True, timeout=300) as r:
            if r.status_code >= 400:
                raise AirdAPIError(
                    f"Share download failed for {file_path} (HTTP {r.status_code})",
                    r.status_code,
                )
            with local_path.open("wb") as f:
                for chunk in r.iter_content(chunk_size=1024 * 1024):
                    if chunk:
                        f.write(chunk)

    def download_share(
        self,
        share_id: str,
        local_dir: Path,
        *,
        share_token: str | None = None,
        workers: int = 2,
        on_progress: Callable[[str], None] | None = None,
    ) -> int:
        share, is_mine = self.find_share(share_id)

        if is_mine and share:
            paths = self.expand_paths_to_files(list(share.get("paths") or []))
            if not paths:
                raise AirdAPIError("No files in share")

            def _own_job(path: str) -> str:
                dest = local_dir / path
                self.clone().download_file(path, dest)
                return path

            jobs = _own_job
            file_paths = paths
        else:
            if share_token:
                self.verify_share(share_id, share_token)
            file_paths = self.share_file_paths(share_id)
            if not file_paths and share:
                file_paths = list(share.get("paths") or [])
            if not file_paths:
                raise AirdAPIError("No files in share (or access denied — try --token)")

            def _shared_job(path: str) -> str:
                dest = local_dir / path
                self.clone().download_share_file(share_id, path, dest)
                return path

            jobs = _shared_job

        count = 0
        with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
            futures = [pool.submit(jobs, p) for p in file_paths]
            for fut in as_completed(futures):
                p = fut.result()
                count += 1
                if on_progress:
                    on_progress(p)
        return count

    def download_all_shares(
        self,
        local_dir: Path,
        *,
        workers: int = 2,
        on_progress: Callable[[str], None] | None = None,
    ) -> int:
        data = self.list_shares()
        share_ids: list[str] = []
        mine = data.get("shares") or {}
        if isinstance(mine, dict):
            share_ids.extend(mine.keys())
        for s in data.get("shared_with_me") or []:
            if isinstance(s, dict) and s.get("id"):
                share_ids.append(s["id"])
        total = 0
        for sid in share_ids:
            dest = local_dir / sid
            dest.mkdir(parents=True, exist_ok=True)
            total += self.download_share(
                sid,
                dest,
                workers=workers,
                on_progress=on_progress,
            )
        return total
