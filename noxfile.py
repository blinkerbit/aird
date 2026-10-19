"""Shared test and quality gate for local runs and GitHub Actions.

    nox -s js quality tests

CI sets AIRD_CI=1 for the tests session so pytest uses the hashed lockfile,
builds the wheel, and includes slow packaging tests.
"""

from __future__ import annotations

import glob
import os

import nox

nox.options.sessions = ["js", "quality", "tests"]


def _npm_ci(session: nox.Session) -> None:
    if not os.path.isdir("node_modules"):
        session.run("npm", "ci", "--ignore-scripts", external=True)


@nox.session(venv_backend="none")
def js(session: nox.Session) -> None:
    """Install Node deps, build the share bundle, and run Vitest."""
    _npm_ci(session)
    session.run("npm", "run", "js:share", external=True)
    session.run("npm", "run", "test:js", external=True)


@nox.session(venv_backend="none")
def quality(session: nox.Session) -> None:
    """ESLint over aird/static/js."""
    _npm_ci(session)
    session.run("npm", "run", "lint:js", external=True)


@nox.session(python="3.12")
def tests(session: nox.Session) -> None:
    """Pytest. AIRD_CI=1 matches the GitHub Actions backend job."""
    if os.environ.get("AIRD_CI") == "1":
        _npm_ci(session)
        session.run("npm", "run", "js:share", external=True)
        session.install(
            "--require-hashes",
            "--only-binary=:all:",
            "--no-binary=http-ece",
            "-r",
            "requirements-ci.txt",
        )
        session.run("python", "-m", "build", "--wheel", "-o", "dist")
        wheels = sorted(glob.glob("dist/aird-*.whl"))
        if not wheels:
            session.error("wheel build produced no dist/aird-*.whl")
        wheel = os.path.abspath(wheels[-1])
        session.install("--no-deps", "--only-binary=:all:", wheel)
        session.run(
            "python",
            "-m",
            "pytest",
            "tests/",
            "-q",
            "--tb=no",
            "-m",
            "slow or not slow",
            "--cov=aird",
            "--cov-report=xml:coverage.xml",
            *session.posargs,
            env={"AIRD_TEST_WHEEL": wheel},
        )
        return

    _npm_ci(session)
    session.run("npm", "run", "js:build", external=True)
    session.install("-r", "requirements-test.txt")
    session.install("-e", ".[chat]")
    session.run("python", "-m", "pytest", "tests/", *session.posargs)
