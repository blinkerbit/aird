"""Pick the PyPI version for a branch channel and optionally write setup.py."""

from __future__ import annotations

import argparse
import re
import sys

_VERSION_RE = re.compile(
    r'version="(?P<major>\d+)\.(?P<minor>\d+)\.(?P<patch>\d+)'
    r'(?P<suffix>(?:\.dev\d+|rc\d+)?)"'
)
_BARE_RE = re.compile(
    r"(?P<major>\d+)\.(?P<minor>\d+)\.(?P<patch>\d+)(?P<suffix>(?:\.dev\d+|rc\d+)?)"
)


def current_version(content: str) -> str:
    match = _VERSION_RE.search(content)
    if not match:
        raise ValueError("version not found in setup.py")
    return f"{match.group('major')}.{match.group('minor')}.{match.group('patch')}{match.group('suffix') or ''}"


def next_version(current: str, channel: str, seq: int) -> str:
    match = _BARE_RE.fullmatch(current)
    if not match:
        raise ValueError(f"bad version {current}")
    major = int(match.group("major"))
    minor = int(match.group("minor"))
    patch = int(match.group("patch"))
    suffix = match.group("suffix") or ""
    prerelease = suffix.startswith("rc") or suffix.startswith(".dev")
    if channel == "main":
        if prerelease:
            return f"{major}.{minor}.{patch}"
        return f"{major}.{minor}.{patch + 1}"
    base = patch if prerelease else patch + 1
    if channel == "rc":
        if seq < 1:
            raise ValueError("rc sequence must be >= 1")
        return f"{major}.{minor}.{base}rc{seq}"
    if channel == "dev":
        if seq < 0:
            raise ValueError("dev sequence must be >= 0")
        return f"{major}.{minor}.{base}.dev{seq}"
    raise ValueError(f"unknown channel {channel}")


def replace_version(content: str, version: str) -> str:
    updated, count = _VERSION_RE.subn(f'version="{version}"', content, count=1)
    if count != 1:
        raise ValueError("version not found in setup.py")
    return updated


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Set the release version in setup.py")
    parser.add_argument("--channel", required=True, choices=("main", "rc", "dev"))
    parser.add_argument("--seq", type=int, default=0)
    parser.add_argument("--setup", default="setup.py")
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args(argv)
    with open(args.setup, encoding="utf-8") as handle:
        content = handle.read()
    version = next_version(current_version(content), args.channel, args.seq)
    if args.write:
        with open(args.setup, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(replace_version(content, version))
    print(version)
    return 0


if __name__ == "__main__":
    sys.exit(main())
