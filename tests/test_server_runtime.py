"""Tests for HTTP worker count calculation."""

from unittest.mock import patch

from aird.server_runtime import (
    compute_default_worker_count,
    detect_physical_cpu_count,
    resolve_worker_count,
)


def test_compute_default_worker_count_formula():
    with patch("aird.server_runtime.os.cpu_count", return_value=8), patch(
        "aird.server_runtime.detect_threads_per_core", return_value=2.0
    ), patch("aird.server_runtime.detect_physical_cpu_count", return_value=4):
        assert compute_default_worker_count() == 10  # ceil(1.25 * 2 * 4)


def test_resolve_worker_count_explicit():
    with patch("aird.server_runtime.sys.platform", "linux"):
        assert resolve_worker_count(3) == 3


def test_resolve_worker_count_windows_forces_one():
    with patch("aird.server_runtime.sys.platform", "win32"):
        assert resolve_worker_count(8) == 1


def test_detect_physical_cpu_count_fallback():
    with patch("aird.server_runtime.os.cpu_count", return_value=8), patch(
        "aird.server_runtime.detect_threads_per_core", return_value=2.0
    ):
        assert detect_physical_cpu_count() == 4
