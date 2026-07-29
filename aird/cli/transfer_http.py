"""Stream HTTP upload/download for aird-cli."""

from __future__ import annotations

from pathlib import Path
from typing import Callable

import requests


def upload_file(
    http: requests.Session,
    base_url: str,
    xsrf_header: dict[str, str],
    local_path: Path,
    remote_dir: str = "",
    *,
    on_progress: Callable[[int, int], None] | None = None,
) -> None:
    total = local_path.stat().st_size
    filename = local_path.name
    uploaded = 0

    class _ProgressReader:
        def __init__(self, fh):
            self._fh = fh

        def read(self, size: int = -1) -> bytes:
            data = self._fh.read(size)
            nonlocal uploaded
            uploaded += len(data)
            if on_progress and data:
                on_progress(uploaded, total)
            return data

    with local_path.open("rb") as fh:
        r = http.post(
            f"{base_url}/upload",
            data=_ProgressReader(fh),
            headers={
                "Content-Type": "application/octet-stream",
                "X-Upload-Dir": remote_dir.strip("/"),
                "X-Upload-Filename": filename,
                **xsrf_header,
            },
            timeout=3600,
        )
    if r.status_code >= 400:
        raise RuntimeError(f"Upload failed ({r.status_code}): {r.text}")


def download_file(
    http: requests.Session,
    base_url: str,
    remote_path: str,
    local_path: Path,
    *,
    on_progress: Callable[[int, int], None] | None = None,
) -> None:
    enc = "/".join(
        requests.utils.quote(p) for p in remote_path.strip("/").split("/") if p
    )
    url = f"{base_url}/files/{enc}?download=1" if enc else f"{base_url}/files/?download=1"
    local_path.parent.mkdir(parents=True, exist_ok=True)
    downloaded = 0
    with http.get(url, stream=True, timeout=3600) as r:
        if r.status_code >= 400:
            raise RuntimeError(f"Download failed ({r.status_code})")
        total = int(r.headers.get("Content-Length") or 0)
        with local_path.open("wb") as out:
            for chunk in r.iter_content(chunk_size=8 * 1024 * 1024):
                if not chunk:
                    continue
                out.write(chunk)
                downloaded += len(chunk)
                if on_progress:
                    on_progress(downloaded, total or downloaded)
