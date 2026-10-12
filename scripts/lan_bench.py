"""Quick LAN upload/download benchmark against AIRD."""
from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

import requests

SERVER = sys.argv[1] if len(sys.argv) > 1 else "http://192.168.5.5:8001"
SESSION = Path.home() / "AppData" / "Roaming" / "aird" / "session.json"
TEST_FILE = Path.home() / "AppData" / "Local" / "Temp" / "aird-bench.bin"


def load_session() -> requests.Session:
    cfg = json.loads(SESSION.read_text(encoding="utf-8"))
    s = requests.Session()
    host = SERVER.split("://", 1)[-1].split("/")[0].split(":")[0]
    for c in cfg.get("cookies") or []:
        s.cookies.set(c["name"], c["value"], domain=host, path="/")
    return s


def mbps(nbytes: int, seconds: float) -> float:
    return (nbytes / 1_000_000) / seconds if seconds > 0 else 0.0


def bench_download(s: requests.Session) -> None:
    url = f"{SERVER.rstrip('/')}/files/aird-bench.bin?download=1"
    out = Path.home() / "AppData" / "Local" / "Temp" / "lan-dl.bin"
    t0 = time.perf_counter()
    n = 0
    with s.get(url, stream=True, timeout=3600) as r:
        print(f"download status={r.status_code} content-length={r.headers.get('Content-Length')}")
        if r.status_code != 200:
            print(r.text[:500])
            return
        with out.open("wb") as f:
            for chunk in r.iter_content(8 * 1024 * 1024):
                if not chunk:
                    continue
                f.write(chunk)
                n += len(chunk)
    dt = time.perf_counter() - t0
    print(f"download: {mbps(n, dt):.1f} MB/s ({dt:.2f}s, {n} bytes)")


def bench_upload(s: requests.Session) -> None:
    if not TEST_FILE.is_file():
        print(f"missing test file {TEST_FILE}")
        return
    xsrf = s.cookies.get("_xsrf", "")
    headers = {
        "Content-Type": "application/octet-stream",
        "X-Upload-Dir": "",
        "X-Upload-Filename": "aird-bench.bin",
    }
    if xsrf:
        headers["X-XSRFToken"] = xsrf
    size = TEST_FILE.stat().st_size
    t0 = time.perf_counter()
    with TEST_FILE.open("rb") as fh:
        r = s.post(f"{SERVER.rstrip('/')}/upload", data=fh, headers=headers, timeout=3600)
    dt = time.perf_counter() - t0
    print(f"upload status={r.status_code}")
    if r.status_code >= 400:
        print(r.text[:500])
        return
    print(f"upload: {mbps(size, dt):.1f} MB/s ({dt:.2f}s, {size} bytes)")


def main() -> None:
    s = load_session()
    s.get(f"{SERVER.rstrip('/')}/login", timeout=30)
    print(f"server={SERVER}")
    bench_upload(s)
    bench_download(s)


if __name__ == "__main__":
    main()
