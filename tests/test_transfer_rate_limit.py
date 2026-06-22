"""Tests for transfer rate limiter."""

import asyncio

from aird.core.rate_limit import TransferRateLimiter


def test_unlimited_by_default():
    TransferRateLimiter.configure(upload_mb_per_sec=0, download_mb_per_sec=0)
    assert TransferRateLimiter.try_acquire_concurrent("user1")
    TransferRateLimiter.release_concurrent("user1")


def test_concurrent_cap():
    TransferRateLimiter.configure(max_concurrent=1)
    assert TransferRateLimiter.try_acquire_concurrent("u")
    assert not TransferRateLimiter.try_acquire_concurrent("u")
    TransferRateLimiter.release_concurrent("u")
    assert TransferRateLimiter.try_acquire_concurrent("u")
    TransferRateLimiter.release_concurrent("u")
    TransferRateLimiter.configure(max_concurrent=0)


def test_bandwidth_wait():
    TransferRateLimiter.configure(upload_mb_per_sec=1, burst_mb=0.001)
    loop = asyncio.new_event_loop()
    try:
        t0 = loop.time()
        loop.run_until_complete(
            TransferRateLimiter.wait_for_bytes("u", 512 * 1024, direction="upload")
        )
        assert loop.time() - t0 >= 0
    finally:
        loop.close()
        TransferRateLimiter.configure(upload_mb_per_sec=0)
