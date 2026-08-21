"""HTTP server process model (socketify is single-process)."""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def resolve_worker_count(configured: int | None = None) -> int:
    """Always 1 — socketify/libuv runs in a single process.

    ``configured`` / ``AIRD_WORKERS`` are ignored; kept for CLI compatibility.
    """
    if configured is not None and configured > 1:
        logger.warning(
            "Ignoring --workers=%s; socketify serves with a single process",
            configured,
        )
    return 1


def describe_worker_layout(worker_count: int = 1) -> str:
    return f"workers={worker_count} (socketify single-process)"
