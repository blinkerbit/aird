"""AIRD1 bulk transfer wire protocol."""

from __future__ import annotations

import struct

MAGIC = b"AIRD1"
VERSION = 1
HEADER_PREFIX_LEN = len(MAGIC) + 1 + 4  # magic + version + ticket_len

OP_PUT = 1
OP_GET = 2

STATUS_OK = 0
STATUS_ERR_GENERIC = 1
STATUS_ERR_TICKET = 2
STATUS_ERR_PATH = 3
STATUS_ERR_SIZE = 4
STATUS_ERR_QUOTA = 5
STATUS_ERR_AUTH = 6

BUFFER_SIZE = 8 * 1024 * 1024
BUFFER_POOL_SIZE = 8


class ProtocolError(ValueError):
    """Malformed bulk protocol frame."""


def pack_client_header(ticket: bytes) -> bytes:
    if not isinstance(ticket, (bytes, bytearray)):
        raise TypeError("ticket must be bytes")
    if len(ticket) > 16 * 1024:
        raise ProtocolError("ticket too large")
    return MAGIC + bytes([VERSION]) + struct.pack(">I", len(ticket)) + bytes(ticket)


def unpack_client_header(data: bytes) -> tuple[bytes, int]:
    """Return (ticket_bytes, total_header_len)."""
    if len(data) < HEADER_PREFIX_LEN:
        raise ProtocolError("header too short")
    if data[: len(MAGIC)] != MAGIC:
        raise ProtocolError("bad magic")
    version = data[len(MAGIC)]
    if version != VERSION:
        raise ProtocolError(f"unsupported version {version}")
    (ticket_len,) = struct.unpack(">I", data[len(MAGIC) + 1 : HEADER_PREFIX_LEN])
    end = HEADER_PREFIX_LEN + ticket_len
    if ticket_len > 16 * 1024 or len(data) < end:
        raise ProtocolError("incomplete ticket")
    return data[HEADER_PREFIX_LEN:end], end
