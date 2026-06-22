"""Per-user transfer rate limiting (token bucket, in-process)."""

from __future__ import annotations

import threading
import time
from typing import Any


class _TokenBucket:
    __slots__ = ("rate", "burst", "tokens", "updated")

    def __init__(self, rate_bytes_per_sec: float, burst_bytes: float) -> None:
        self.rate = max(rate_bytes_per_sec, 1.0)
        self.burst = max(burst_bytes, self.rate)
        self.tokens = self.burst
        self.updated = time.monotonic()

    def consume(self, nbytes: int) -> float:
        """Return seconds to wait before nbytes can proceed (0 = ok now)."""
        now = time.monotonic()
        elapsed = now - self.updated
        self.updated = now
        self.tokens = min(self.burst, self.tokens + elapsed * self.rate)
        if nbytes <= self.tokens:
            self.tokens -= nbytes
            return 0.0
        deficit = nbytes - self.tokens
        self.tokens = 0.0
        return deficit / self.rate


class TransferRateLimiter:
    """Token-bucket limits for upload/download bytes per user."""

    _lock = threading.Lock()
    _buckets: dict[str, _TokenBucket] = {}
    _upload_rate: float = 0.0
    _download_rate: float = 0.0
    _burst_mb: float = 64.0
    _max_concurrent: int = 0
    _active: dict[str, int] = {}

    @classmethod
    def configure(
        cls,
        *,
        upload_mb_per_sec: float = 0.0,
        download_mb_per_sec: float = 0.0,
        burst_mb: float = 64.0,
        max_concurrent: int = 0,
    ) -> None:
        cls._upload_rate = max(0.0, upload_mb_per_sec) * 1024 * 1024
        cls._download_rate = max(0.0, download_mb_per_sec) * 1024 * 1024
        cls._burst_mb = max(1.0, burst_mb)
        cls._max_concurrent = max(0, max_concurrent)

    @classmethod
    def _bucket(cls, key: str, rate: float) -> _TokenBucket | None:
        if rate <= 0:
            return None
        burst = cls._burst_mb * 1024 * 1024
        with cls._lock:
            bucket = cls._buckets.get(key)
            if bucket is None or bucket.rate != rate or bucket.burst != burst:
                bucket = _TokenBucket(rate, burst)
                cls._buckets[key] = bucket
            return bucket

    @classmethod
    async def wait_for_bytes(
        cls, username: str, nbytes: int, *, direction: str
    ) -> None:
        import asyncio

        rate = cls._upload_rate if direction == "upload" else cls._download_rate
        bucket = cls._bucket(f"{direction}:{username}", rate)
        if bucket is None:
            return
        wait = bucket.consume(nbytes)
        if wait > 0:
            await asyncio.sleep(wait)

    @classmethod
    def try_acquire_concurrent(cls, username: str) -> bool:
        if cls._max_concurrent <= 0:
            return True
        with cls._lock:
            count = cls._active.get(username, 0)
            if count >= cls._max_concurrent:
                return False
            cls._active[username] = count + 1
            return True

    @classmethod
    def release_concurrent(cls, username: str) -> None:
        if cls._max_concurrent <= 0:
            return
        with cls._lock:
            count = cls._active.get(username, 0)
            if count <= 1:
                cls._active.pop(username, None)
            else:
                cls._active[username] = count - 1

    @classmethod
    def apply_transfer_config(cls, cfg: dict[str, Any]) -> None:
        cls.configure(
            upload_mb_per_sec=float(cfg.get("upload_mb_per_sec", 0) or 0),
            download_mb_per_sec=float(cfg.get("download_mb_per_sec", 0) or 0),
            burst_mb=float(cfg.get("burst_mb", 64) or 64),
            max_concurrent=int(cfg.get("max_concurrent", 0) or 0),
        )
