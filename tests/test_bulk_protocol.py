"""Tests for AIRD1 bulk protocol."""

from __future__ import annotations

import pytest

from aird.core.bulk.protocol import (
    MAGIC,
    ProtocolError,
    pack_client_header,
    unpack_client_header,
)


def test_pack_unpack_roundtrip():
    ticket = b"test-ticket-bytes-12345"
    packed = pack_client_header(ticket)
    assert packed.startswith(MAGIC)
    out, used = unpack_client_header(packed)
    assert out == ticket
    assert used == len(packed)


def test_unpack_rejects_bad_magic():
    with pytest.raises(ProtocolError):
        unpack_client_header(b"XXXX1" + b"\x01" + b"\x00\x00\x00\x05" + b"hello")
