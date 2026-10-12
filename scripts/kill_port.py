#!/usr/bin/env python3
"""Kill every process listening on one or more TCP ports (Windows and Linux).

Usage:
  python scripts/kill_port.py 8001
  python scripts/kill_port.py 8001 8002
"""

from __future__ import annotations

import argparse
import os
import re
import signal
import subprocess
import sys
import time

_LISTEN_PID = re.compile(r"LISTENING\s+(\d+)\s*$", re.I)
_SS_PID = re.compile(r"pid=(\d+)")


def _run(cmd: list[str]) -> str:
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    except FileNotFoundError:
        return ""
    return (proc.stdout or "") + (proc.stderr or "")


def _pids_windows(port: int) -> set[int]:
    pids: set[int] = set()
    for line in _run(["netstat", "-ano"]).splitlines():
        if "LISTENING" not in line.upper():
            continue
        if not re.search(rf"[:\]]{port}(?:\s|$)", line):
            continue
        match = _LISTEN_PID.search(line)
        if match:
            pids.add(int(match.group(1)))
    return pids


def _pids_unix(port: int) -> set[int]:
    pids: set[int] = set()
    for raw in _run(["lsof", f"-tiTCP:{port}", "-sTCP:LISTEN"]).split():
        if raw.isdigit():
            pids.add(int(raw))
    if pids:
        return pids
    for raw in _run(["fuser", "-n", "tcp", str(port)]).split():
        if raw.isdigit():
            pids.add(int(raw))
    if pids:
        return pids
    for line in _run(["ss", "-ltnp", f"sport = :{port}"]).splitlines():
        pids.update(int(m) for m in _SS_PID.findall(line))
    return pids


def pids_on_port(port: int) -> set[int]:
    found = _pids_windows(port) if os.name == "nt" else _pids_unix(port)
    found.discard(os.getpid())
    found.discard(0)
    if os.name == "nt":
        found.discard(4)
    return found


def kill_pid(pid: int) -> bool:
    if os.name == "nt":
        proc = subprocess.run(
            ["taskkill", "/F", "/PID", str(pid)],
            capture_output=True,
            text=True,
            check=False,
        )
        return proc.returncode == 0
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        return True
    except PermissionError:
        return False
    for _ in range(10):
        time.sleep(0.05)
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return True
    try:
        os.kill(pid, signal.SIGKILL)
    except ProcessLookupError:
        return True
    except PermissionError:
        return False
    time.sleep(0.05)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return True
    return False


def main() -> int:
    parser = argparse.ArgumentParser(description="Kill processes listening on TCP ports.")
    parser.add_argument("ports", nargs="+", type=int, metavar="PORT")
    parser.add_argument("--dry-run", action="store_true", help="Print PIDs without killing")
    args = parser.parse_args()

    failed = 0
    for port in args.ports:
        if not (1 <= port <= 65535):
            print(f"invalid port: {port}", file=sys.stderr)
            failed += 1
            continue
        pids = pids_on_port(port)
        if not pids:
            print(f"port {port}: idle")
            continue
        for pid in sorted(pids):
            if args.dry_run:
                print(f"port {port}: pid {pid}")
                continue
            if kill_pid(pid):
                print(f"port {port}: killed pid {pid}")
            else:
                print(f"port {port}: FAILED pid {pid}", file=sys.stderr)
                failed += 1
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
