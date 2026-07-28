"""Faster WebSocket frame masking for Tornado.

Tornado's pure-Python ``_websocket_mask_python`` XORs each byte in a plain
Python ``for`` loop. For AIRD's bulk-transfer WebSocket (client sends 256 KiB
binary frames per chunk), that loop runs on every incoming frame and blocks
the single Tornado IOLoop thread for tens of milliseconds each time — during
which the *entire* server (all other requests, other users' transfers, even
UI-only endpoints) is unresponsive. On a fast LAN/WireGuard link streaming
many frames per second, this can stall the process badly enough to require
killing it.

This replaces the masking routine with an equivalent big-integer XOR, which
CPython implements as a tight C loop over machine words instead of Python
bytecode per byte — orders of magnitude faster for large payloads and
produces byte-identical output to the RFC 6455 masking algorithm.
"""

from __future__ import annotations

import logging

import tornado.util
import tornado.websocket

logger = logging.getLogger(__name__)


def fast_websocket_mask(mask: bytes, data: bytes) -> bytes:
    if len(mask) != 4:
        raise ValueError("mask must be 4 bytes")
    n = len(data)
    if n == 0:
        return b""
    mask_full = (mask * ((n // 4) + 1))[:n]
    data_int = int.from_bytes(data, "big")
    mask_int = int.from_bytes(mask_full, "big")
    return (data_int ^ mask_int).to_bytes(n, "big")


_patched = False


def patch() -> None:
    """Swap the fast masking function into every place Tornado reads it from.

    Tornado's websocket module does ``from tornado.util import
    _websocket_mask``, which binds the name into its own module namespace at
    import time — patching ``tornado.util`` alone would not be picked up by
    already-imported callers, so both module attributes are patched.
    """
    global _patched
    if _patched:
        return
    tornado.util._websocket_mask = fast_websocket_mask
    tornado.websocket._websocket_mask = fast_websocket_mask
    _patched = True
    logger.debug("Patched Tornado WebSocket masking with fast big-integer XOR implementation")
