"""Ensure release wheels ship UI static assets (logos, feature flags JS)."""

from __future__ import annotations

import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

REQUIRED_IN_WHEEL = (
    "aird/static/img/logo-icon.png",
    "aird/static/img/logo-text.png",
    "aird/static/img/logo.png",
    "aird/static/js/feature-flags-live.js",
    "aird/static/favicon.png",
)

REQUIRED_IN_SOURCE = tuple(p.replace("aird/", "aird/") for p in REQUIRED_IN_WHEEL)


@pytest.mark.parametrize("rel", REQUIRED_IN_SOURCE)
def test_static_assets_exist_in_source_tree(rel: str) -> None:
    path = ROOT / rel
    assert path.is_file(), f"missing source asset: {rel}"


def _wheel_members(wheel: Path) -> set[str]:
    with zipfile.ZipFile(wheel) as zf:
        return set(zf.namelist())


def test_built_wheel_includes_required_static_assets(tmp_path: Path) -> None:
    out = tmp_path / "dist"
    subprocess.run(
        [sys.executable, "-m", "build", "--wheel", "-o", str(out)],
        cwd=ROOT,
        check=True,
        capture_output=True,
    )
    wheels = list(out.glob("aird-*.whl"))
    assert wheels, "build produced no wheel"
    members = _wheel_members(wheels[0])
    missing = [p for p in REQUIRED_IN_WHEEL if p not in members]
    assert not missing, f"wheel missing: {missing}"
