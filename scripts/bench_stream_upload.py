#!/usr/bin/env python3
"""Benchmark single-stream POST /upload against a running Aird server."""

from __future__ import annotations

import argparse
import os
import sys
import tempfile
import time

import requests

from aird.cli.transfer_http import upload_file


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


def _make_file(size: int) -> str:
    fd, path = tempfile.mkstemp(prefix="aird_bench_stream_")
    os.close(fd)
    chunk = b"x" * min(8 * 1024 * 1024, size)
    with open(path, "wb") as fh:
        remaining = size
        while remaining > 0:
            n = min(len(chunk), remaining)
            fh.write(chunk[:n])
            remaining -= n
    return path


def bench(base: str, token: str, size_mb: int, label: str) -> float:
    size = size_mb * 1024 * 1024
    path = _make_file(size)
    session = requests.Session()
    try:
        _login(session, base, token)
        t0 = time.perf_counter()
        upload_file(session, base.rstrip("/"), _xsrf(session), __import__("pathlib").Path(path), "")
        elapsed = time.perf_counter() - t0
        mb_s = (size / (1024 * 1024)) / elapsed if elapsed > 0 else 0.0
        print(f"{label}: {mb_s:.1f} MB/s ({elapsed:.2f}s, {size_mb} MiB)")
        return mb_s
    finally:
        os.unlink(path)


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--url", default="http://127.0.0.1:8001")
    p.add_argument("--token", default=os.environ.get("AIRD_BENCH_TOKEN", ""))
    p.add_argument("--size-mb", type=int, default=256)
    p.add_argument("--label", default="stream-upload")
    args = p.parse_args()
    if not args.token:
        print("Set --token or AIRD_BENCH_TOKEN", file=sys.stderr)
        return 1
    bench(args.url, args.token, args.size_mb, args.label)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
