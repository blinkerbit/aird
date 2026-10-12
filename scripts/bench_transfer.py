#!/usr/bin/env python3
"""Benchmark parallel HTTP Range transfers against a running Aird server."""

from __future__ import annotations

import argparse
import os
import sys
import tempfile
import time
from pathlib import Path

import requests

from aird.cli.transfer_http import (
    DEFAULT_CHUNK,
    DEFAULT_CONCURRENCY,
    download_file_ranged,
    upload_file_ranged,
)


def _login(session: requests.Session, base: str, token: str) -> None:
    r = session.get(f"{base}/login", timeout=30)
    r.raise_for_status()
    xsrf = session.cookies.get("_xsrf", "")
    r = session.post(
        f"{base}/login",
        data={"username": "token", "password": token, "_xsrf": xsrf},
        timeout=30,
    )
    if r.status_code >= 400:
        raise RuntimeError(f"Login failed ({r.status_code})")


def _xsrf(session: requests.Session) -> dict[str, str]:
    tok = session.cookies.get("_xsrf", "")
    return {"X-XSRFToken": tok} if tok else {}


def _make_file(size: int) -> Path:
    fd, path = tempfile.mkstemp(prefix="aird_bench_")
    os.close(fd)
    p = Path(path)
    with p.open("wb") as f:
        remaining = size
        chunk = b"x" * min(1024 * 1024, size)
        while remaining > 0:
            n = min(len(chunk), remaining)
            f.write(chunk[:n])
            remaining -= n
    return p


def bench_upload(
    session: requests.Session,
    base: str,
    size: int,
    workers: int,
    chunk: int,
) -> float:
    path = _make_file(size)
    try:
        t0 = time.perf_counter()
        upload_file_ranged(
            session,
            base,
            _xsrf(session),
            path,
            "",
            chunk_size=chunk,
            workers=workers,
        )
        elapsed = time.perf_counter() - t0
        mb_s = (size / (1024 * 1024)) / elapsed if elapsed > 0 else 0
        print(f"upload {size} bytes workers={workers} chunk={chunk}: {mb_s:.1f} MB/s ({elapsed:.2f}s)")
        return mb_s
    finally:
        path.unlink(missing_ok=True)


def bench_download(
    session: requests.Session,
    base: str,
    remote_name: str,
    size: int,
    workers: int,
    chunk: int,
) -> float:
    out = Path(tempfile.mkstemp(prefix="aird_bench_dl_")[1])
    try:
        t0 = time.perf_counter()
        download_file_ranged(
            session,
            base,
            remote_name,
            out,
            chunk_size=chunk,
            workers=workers,
        )
        elapsed = time.perf_counter() - t0
        mb_s = (size / (1024 * 1024)) / elapsed if elapsed > 0 else 0
        print(f"download {size} bytes workers={workers} chunk={chunk}: {mb_s:.1f} MB/s ({elapsed:.2f}s)")
        return mb_s
    finally:
        out.unlink(missing_ok=True)


def main() -> int:
    p = argparse.ArgumentParser(description="Aird transfer benchmark")
    p.add_argument("--url", default=os.environ.get("AIRD_BENCH_URL", "http://127.0.0.1:8080"))
    p.add_argument("--token", default=os.environ.get("AIRD_BENCH_TOKEN", ""))
    p.add_argument("--size-mb", type=int, default=100)
    p.add_argument("--workers", type=int, default=DEFAULT_CONCURRENCY)
    p.add_argument("--chunk-mb", type=int, default=DEFAULT_CHUNK // (1024 * 1024))
    args = p.parse_args()
    if not args.token:
        print("Set --token or AIRD_BENCH_TOKEN", file=sys.stderr)
        return 1

    base = args.url.rstrip("/")
    size = args.size_mb * 1024 * 1024
    chunk = args.chunk_mb * 1024 * 1024
    session = requests.Session()
    _login(session, base, args.token)

    remote = f"bench_{int(time.time())}.bin"
    path = _make_file(size)
    try:
        upload_file_ranged(session, base, _xsrf(session), path, "", chunk_size=chunk, workers=1)
    finally:
        path.unlink(missing_ok=True)

    bench_upload(session, base, size, 1, chunk)
    bench_upload(session, base, size, args.workers, chunk)
    bench_download(session, base, remote, size, 1, chunk)
    bench_download(session, base, remote, size, args.workers, chunk)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
