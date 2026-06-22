"""Parallel HTTP Range upload/download for aird-cli."""

from __future__ import annotations

import math
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Callable

import requests


DEFAULT_CHUNK = 32 * 1024 * 1024
DEFAULT_CONCURRENCY = 4


def _clone_session(http: requests.Session) -> requests.Session:
    s = requests.Session()
    s.cookies.update(http.cookies)
    s.headers.update(getattr(http, "headers", {}))
    return s


def _range_session(
    http: requests.Session,
    base_url: str,
    xsrf_header: dict[str, str],
    upload_dir: str,
    filename: str,
    total_size: int,
) -> str:
    r = http.post(
        f"{base_url}/api/upload/range/session",
        json={
            "upload_dir": upload_dir.strip("/"),
            "filename": filename,
            "total_size": total_size,
        },
        headers={"Content-Type": "application/json", **xsrf_header},
        timeout=120,
    )
    if r.status_code >= 400:
        raise RuntimeError(f"Range session failed ({r.status_code}): {r.text}")
    return r.json()["upload_id"]


def _put_chunk(
    http: requests.Session,
    base_url: str,
    xsrf_header: dict[str, str],
    upload_id: str,
    data: bytes,
    start: int,
    end: int,
    total_size: int,
) -> bool:
    r = http.put(
        f"{base_url}/api/upload/range/{upload_id}",
        data=data,
        headers={
            "Content-Type": "application/octet-stream",
            "Content-Range": f"bytes {start}-{end}/{total_size}",
            **xsrf_header,
        },
        timeout=600,
    )
    if r.status_code == 201:
        return True
    if r.status_code not in (200, 201):
        raise RuntimeError(f"Chunk upload failed ({r.status_code}): {r.text}")
    return False


def upload_file_ranged(
    http: requests.Session,
    base_url: str,
    xsrf_header: dict[str, str],
    local_path: Path,
    remote_dir: str = "",
    *,
    chunk_size: int = DEFAULT_CHUNK,
    workers: int = DEFAULT_CONCURRENCY,
    on_progress: Callable[[int, int], None] | None = None,
) -> None:
    total = local_path.stat().st_size
    filename = local_path.name
    upload_id = _range_session(
        http, base_url, xsrf_header, remote_dir, filename, total
    )
    total_chunks = math.ceil(total / chunk_size)
    done_bytes = 0
    lock = __import__("threading").Lock()

    def _job(idx: int) -> bool:
        start = idx * chunk_size
        end = min(start + chunk_size, total) - 1
        with local_path.open("rb") as f:
            f.seek(start)
            data = f.read(end - start + 1)
        return _put_chunk(
            _clone_session(http),
            base_url,
            xsrf_header,
            upload_id,
            data,
            start,
            end,
            total,
        )

    finished = False
    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        futures = {pool.submit(_job, i): i for i in range(total_chunks)}
        for fut in as_completed(futures):
            if fut.result():
                finished = True
            with lock:
                done_bytes = min(total, done_bytes + chunk_size)
                if on_progress:
                    on_progress(min(done_bytes, total), total)
    if not finished and done_bytes < total:
        raise RuntimeError("Ranged upload did not complete")


def download_file_ranged(
    http: requests.Session,
    base_url: str,
    remote_path: str,
    local_path: Path,
    *,
    chunk_size: int = DEFAULT_CHUNK,
    workers: int = DEFAULT_CONCURRENCY,
    on_progress: Callable[[int, int], None] | None = None,
) -> None:
    enc = "/".join(requests.utils.quote(p) for p in remote_path.strip("/").split("/") if p)
    url = f"{base_url}/files/{enc}?download=1" if enc else f"{base_url}/files/?download=1"
    head = http.head(url, timeout=120)
    if head.status_code >= 400:
        raise RuntimeError(f"HEAD failed ({head.status_code})")
    total = int(head.headers.get("Content-Length") or 0)
    if total <= 0:
        raise RuntimeError("Missing Content-Length for ranged download")

    local_path.parent.mkdir(parents=True, exist_ok=True)
    with local_path.open("wb") as out:
        out.truncate(total)

    total_chunks = math.ceil(total / chunk_size)
    done_bytes = 0
    lock = __import__("threading").Lock()

    def _job(idx: int) -> int:
        start = idx * chunk_size
        end = min(start + chunk_size, total) - 1
        sess = _clone_session(http)
        r = sess.get(
            url,
            headers={"Range": f"bytes={start}-{end}"},
            timeout=600,
        )
        if r.status_code not in (200, 206):
            raise RuntimeError(f"Range GET failed ({r.status_code})")
        with local_path.open("r+b") as f:
            f.seek(start)
            f.write(r.content)
        return len(r.content)

    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        futures = [pool.submit(_job, i) for i in range(total_chunks)]
        for fut in as_completed(futures):
            n = fut.result()
            with lock:
                done_bytes += n
                if on_progress:
                    on_progress(min(done_bytes, total), total)
