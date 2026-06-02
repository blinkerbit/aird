"""HTTP server process count and Tornado prefork helpers."""

from __future__ import annotations

import logging
import math
import os
import sys

logger = logging.getLogger(__name__)


def detect_threads_per_core() -> float:
    """Logical CPUs per physical core (hyperthreading); default 2."""
    raw = os.environ.get("AIRD_THREADS_PER_CORE", "").strip()
    if raw:
        try:
            value = float(raw)
            if value > 0:
                return value
        except ValueError:
            logger.warning("Invalid AIRD_THREADS_PER_CORE=%r; using 2", raw)
    return 2.0


def detect_physical_cpu_count() -> int:
    """Best-effort physical core count; falls back to logical / threads_per_core."""
    logical = os.cpu_count() or 1
    threads_per_core = detect_threads_per_core()
    if sys.platform == "linux":
        try:
            ids: set[int] = set()
            with open("/proc/cpuinfo", encoding="utf-8", errors="replace") as cpuinfo:
                for line in cpuinfo:
                    if line.lower().startswith("core id") or line.lower().startswith(
                        "cpu cores"
                    ):
                        _, _, val = line.partition(":")
                        ids.add(int(val.strip()))
            if ids:
                return max(1, len(ids))
        except OSError:
            pass
    return max(1, round(logical / threads_per_core))


def compute_default_worker_count() -> int:
    """
    Process count = ceil(1.25 * threads_per_core * physical_cores).

    Example: 4 physical cores, 2 threads/core -> ceil(1.25 * 2 * 4) = 10 workers.
    """
    threads_per_core = detect_threads_per_core()
    physical = detect_physical_cpu_count()
    return max(1, math.ceil(1.25 * threads_per_core * physical))


def resolve_worker_count(configured: int | None = None) -> int:
    """CLI/config > env AIRD_WORKERS > computed default; 1 on Windows."""
    if sys.platform == "win32":
        if configured and configured > 1:
            logger.warning(
                "Multiprocess serving is not supported on Windows; using 1 worker"
            )
        return 1
    if configured is not None and configured > 0:
        return configured
    env_workers = os.environ.get("AIRD_WORKERS", "").strip()
    if env_workers:
        try:
            parsed = int(env_workers)
            if parsed > 0:
                return parsed
        except ValueError:
            logger.warning("Invalid AIRD_WORKERS=%r; using default formula", env_workers)
    return compute_default_worker_count()


def describe_worker_layout(worker_count: int) -> str:
    logical = os.cpu_count() or 1
    tpc = detect_threads_per_core()
    physical = detect_physical_cpu_count()
    return (
        f"workers={worker_count} "
        f"(logical_cpus={logical}, physical_cpus={physical}, "
        f"threads_per_core={tpc:g}, formula=ceil(1.25*tpc*physical))"
    )
