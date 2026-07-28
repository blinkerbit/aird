"""Tests for bulk HMAC tickets."""

from __future__ import annotations

import time

import pytest

from aird.core.bulk.ticket import BulkTicketClaims, mint_bulk_ticket, verify_bulk_ticket


def test_ticket_roundtrip():
    secret = "test-secret-key"
    claims = BulkTicketClaims(
        op="PUT",
        username="alice",
        size=1024,
        exp=int(time.time()) + 60,
        upload_dir="docs",
        filename="file.bin",
    )
    token = mint_bulk_ticket(secret, claims)
    verified = verify_bulk_ticket(secret, token)
    assert verified.op == "PUT"
    assert verified.username == "alice"
    assert verified.size == 1024
    assert verified.filename == "file.bin"


def test_ticket_expired():
    secret = "k"
    claims = BulkTicketClaims(
        op="GET",
        username="bob",
        size=1,
        exp=int(time.time()) - 10,
        relpath="a.txt",
    )
    token = mint_bulk_ticket(secret, claims)
    with pytest.raises(ValueError, match="expired"):
        verify_bulk_ticket(secret, token)
