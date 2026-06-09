"""Ensure release wheels ship all package data and template-referenced static assets."""

from __future__ import annotations

import re
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PKG = ROOT / "aird"

PACKAGE_DATA_DIRS = (PKG / "templates", PKG / "static")
PACKAGE_DATA_SUFFIXES = {
    ".html",
    ".css",
    ".js",
    ".png",
    ".ico",
    ".svg",
    ".jpg",
    ".jpeg",
    ".webp",
    ".gif",
}

STATIC_REF_RE = re.compile(r"""/static/([A-Za-z0-9_./-]+)""")


def _source_package_data_paths() -> set[str]:
    paths: set[str] = set()
    for base in PACKAGE_DATA_DIRS:
        if not base.is_dir():
            continue
        for path in base.rglob("*"):
            if not path.is_file():
                continue
            if path.suffix.lower() not in PACKAGE_DATA_SUFFIXES:
                continue
            paths.add(path.relative_to(ROOT).as_posix())
    return paths


def _source_python_modules() -> set[str]:
    modules: set[str] = set()
    for path in PKG.rglob("*.py"):
        if "__pycache__" in path.parts:
            continue
        modules.add(path.relative_to(ROOT).as_posix())
    return modules


def _template_referenced_static_paths() -> set[str]:
    refs: set[str] = set()
    templates = PKG / "templates"
    for path in templates.rglob("*.html"):
        text = path.read_text(encoding="utf-8")
        for match in STATIC_REF_RE.findall(text):
            clean = match.split("?", 1)[0].strip("/")
            if clean:
                refs.add(f"aird/static/{clean}")
    return refs


def _wheel_members(wheel: Path) -> set[str]:
    with zipfile.ZipFile(wheel) as zf:
        return {name for name in zf.namelist() if not name.endswith("/")}


@pytest.fixture(scope="module")
def built_wheel(tmp_path_factory: pytest.TempPathFactory) -> Path:
    out = tmp_path_factory.mktemp("dist")
    subprocess.run(
        [sys.executable, "-m", "build", "--wheel", "-o", str(out)],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    wheels = sorted(out.glob("aird-*.whl"))
    assert wheels, "build produced no wheel"
    return wheels[-1]


@pytest.mark.parametrize("rel", sorted(_source_package_data_paths()))
def test_package_data_exists_in_source_tree(rel: str) -> None:
    assert (ROOT / rel).is_file(), f"missing source asset: {rel}"


def test_built_wheel_includes_all_package_data(built_wheel: Path) -> None:
    members = _wheel_members(built_wheel)
    missing = sorted(_source_package_data_paths() - members)
    assert not missing, f"wheel missing package data ({len(missing)}):\n" + "\n".join(missing)


def test_built_wheel_includes_all_python_modules(built_wheel: Path) -> None:
    members = _wheel_members(built_wheel)
    missing = sorted(_source_python_modules() - members)
    assert not missing, f"wheel missing python modules ({len(missing)}):\n" + "\n".join(missing)


def test_built_wheel_includes_template_referenced_static_assets(built_wheel: Path) -> None:
    members = _wheel_members(built_wheel)
    refs = _template_referenced_static_paths()
    missing = sorted(refs - members)
    assert not missing, (
        f"wheel missing static assets referenced by templates ({len(missing)}):\n"
        + "\n".join(missing)
    )
