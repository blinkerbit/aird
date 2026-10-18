#!/usr/bin/env python3
"""Build the generated frontend bundles served from ``aird/static``.

Bundles (``aird-core.js``, ``share/app.js``) are build artifacts, not source:
they are git-ignored and regenerated here so that ``pip install .``,
``python -m build``, and the PyPI release all ship working assets without
requiring committed bundles. Also runnable directly::

    python scripts/build_frontend.py

Fails closed when Node/npm or the esbuild install is missing: shipping a
wheel without the bundles the templates reference would be worse than a
loud build error.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# (node build script, output it must produce). Deliberately npm-free: only
# `node` plus the vendored node_modules tree are required.
BUNDLES = (
    ("scripts/gen-core-shim.mjs", ROOT / "aird/static/js/core/shim.js"),
    ("scripts/build-core.mjs", ROOT / "aird/static/js/aird-core.js"),
    ("scripts/build-share.mjs", ROOT / "aird/static/js/share/app.js"),
)

OUTPUTS = frozenset(output for _, output in BUNDLES)


def _newest_input_mtime() -> float:
    """Newest mtime of every file the bundles are built from."""
    newest = 0.0
    js_roots = [ROOT / "aird/static/js", ROOT / "scripts", ROOT / "package.json"]
    for base in js_roots:
        if base.is_file():
            newest = max(newest, base.stat().st_mtime)
            continue
        for path in base.rglob("*"):
            if path.is_file() and path not in OUTPUTS and path.suffix != ".map":
                newest = max(newest, path.stat().st_mtime)
    return newest


def _bundles_up_to_date() -> bool:
    """True when every output exists and is newer than every input.

    Lets ``pip install`` from an sdist (which ships freshly built bundles)
    skip the Node toolchain entirely; a source checkout with missing or
    stale bundles still rebuilds.
    """
    try:
        oldest_output = min(out.stat().st_mtime for _, out in BUNDLES)
    except FileNotFoundError:
        return False
    return oldest_output >= _newest_input_mtime()


def main() -> int:
    if _bundles_up_to_date():
        print("build_frontend: bundles up to date (skipped rebuild)")
        return 0
    node = shutil.which("node")
    if node is None:
        print(
            "build_frontend: node not found on PATH; install Node.js to build "
            "the aird frontend bundles (aird-core.js, share/app.js).",
            file=sys.stderr,
        )
        return 1
    if not (ROOT / "node_modules/esbuild").is_dir():
        print(
            "build_frontend: node_modules/esbuild missing; run `npm ci` first.",
            file=sys.stderr,
        )
        return 1
    for script, output in BUNDLES:
        print(f"build_frontend: node {script}", flush=True)
        try:
            subprocess.run(
                [node, script], cwd=ROOT, check=True, capture_output=True, text=True
            )
        except subprocess.CalledProcessError as exc:
            print(f"build_frontend: `node {script}` failed:\n{exc.stderr}", file=sys.stderr)
            return 1
        if not output.is_file():
            print(
                f"build_frontend: `node {script}` produced no {output.relative_to(ROOT)}",
                file=sys.stderr,
            )
            return 1
    print("build_frontend: bundles up to date")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
