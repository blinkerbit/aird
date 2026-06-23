"""Concurrency safety checks for free-threaded (nogil) runtime."""

from __future__ import annotations

import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed

import pytest

from aird.core.rate_limit import TransferRateLimiter
from aird.db.sync import ThreadSafeConnection, db_sync, wrap_connection


@pytest.fixture(autouse=True)
def _reset_rate_limiter():
    TransferRateLimiter.configure(
        upload_mb_per_sec=1.0,
        download_mb_per_sec=0.0,
        burst_mb=1.0,
        max_concurrent=0,
    )
    TransferRateLimiter._buckets.clear()
    yield
    TransferRateLimiter._buckets.clear()


def test_thread_safe_connection_serializes_writes():
    raw = sqlite3.connect(":memory:", check_same_thread=False)
    conn = wrap_connection(raw)
    conn.execute("CREATE TABLE t (id INTEGER PRIMARY KEY, v TEXT)")
    conn.commit()

    def insert_row(i: int) -> None:
        conn.execute("INSERT INTO t (v) VALUES (?)", (str(i),))
        conn.commit()

    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(insert_row, range(40)))

    row = conn.execute("SELECT COUNT(*) FROM t").fetchone()[0]
    assert row == 40


def test_db_sync_is_reentrant():
    depth = []

    def nested():
        with db_sync():
            depth.append(1)
            with db_sync():
                depth.append(2)

    nested()
    assert depth == [1, 2]


def test_rate_limiter_token_bucket_under_parallel_threads():
    TransferRateLimiter.configure(upload_mb_per_sec=8.0, burst_mb=1.0)
    errors = []

    def consume_once():
        try:
            with TransferRateLimiter._lock:
                bucket = TransferRateLimiter._bucket_locked("upload:u", 8.0 * 1024 * 1024)
                assert bucket is not None
                bucket.consume(128 * 1024)
        except Exception as exc:
            errors.append(exc)

    with ThreadPoolExecutor(max_workers=16) as pool:
        futures = [pool.submit(consume_once) for _ in range(64)]
        for fut in as_completed(futures):
            fut.result()

    assert not errors
    with TransferRateLimiter._lock:
        bucket = TransferRateLimiter._buckets.get("upload:u")
        assert bucket is not None
        assert bucket.tokens >= 0
