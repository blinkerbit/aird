#!/usr/bin/env python3
"""Benchmark bulk TCP PUT/GET against a running Aird server."""

from __future__ import annotations

import argparse
import os
import sys
import tempfile
import time
from pathlib import Path

import requests

from aird.cli.bulk_transfer import bulk_get_file, bulk_put_file


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


def _make_file(size: int) -> Path:
    fd, path = tempfile.mkstemp(prefix="aird_bench_bulk_")
    os.close(fd)
    p = Path(path)
    chunk = b"x" * min(1024 * 1024, size)
    with p.open("wb") as f:
        remaining = size
        while remaining > 0:
            n = min(len(chunk), remaining)
            f.write(chunk[:n])
            remaining -= n
    return p


def bench_put(session: requests.Session, base: str, size: int) -> float:
    path = _make_file(size)
    try:
        t0 = time.perf_counter()
        bulk_put_file(session, base, path, "", filename=path.name)
        elapsed = time.perf_counter() - t0
        mb_s = (size / (1024 * 1024)) / elapsed if elapsed > 0 else 0
        print(f"PUT {size} bytes: {mb_s:.1f} MB/s ({elapsed:.2f}s)")
        return mb_s
    finally:
        path.unlink(missing_ok=True)


def bench_get(session: requests.Session, base: str, relpath: str, size: int) -> float:
    out = Path(tempfile.mkstemp(prefix="aird_bench_bulk_dl_")[1])
    try:
        t0 = time.perf_counter()
        bulk_get_file(session, base, relpath, out)
        elapsed = time.perf_counter() - t0
        mb_s = (size / (1024 * 1024)) / elapsed if elapsed > 0 else 0
        print(f"GET {size} bytes: {mb_s:.1f} MB/s ({elapsed:.2f}s)")
        return mb_s
    finally:
        out.unlink(missing_ok=True)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Benchmark Aird bulk TCP transfers")
    p.add_argument("--base", required=True, help="Server base URL")
    p.add_argument("--token", help="Access token for login")
    p.add_argument("--size-mb", type=int, default=512, help="Payload size in MiB")
    p.add_argument("--put-only", action="store_true")
    p.add_argument("--get-only", action="store_true")
    args = p.parse_args(argv)

    gil = getattr(sys, "_is_gil_enabled", None)
    if callable(gil):
        print(f"GIL enabled: {gil()}")
    else:
        print("GIL status: unknown (not Python 3.13+)")

    session = requests.Session()
    if args.token:
        _login(session, args.base.rstrip("/"), args.token)
    else:
        print("Warning: no --token; ensure session cookies are configured", file=sys.stderr)

    size = args.size_mb * 1024 * 1024
    remote_name = f"bench_bulk_{int(time.time())}.bin"

    if not args.get_only:
        path = _make_file(size)
        try:
            t0 = time.perf_counter()
            bulk_put_file(session, args.base, path, "", filename=remote_name)
            elapsed = time.perf_counter() - t0
            print(f"PUT {size} bytes: {(size / (1024 * 1024)) / elapsed:.1f} MB/s")
        finally:
            path.unlink(missing_ok=True)

    if not args.put_only:
        bench_get(session, args.base, remote_name, size)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
