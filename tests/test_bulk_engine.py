"""Integration tests for bulk engine over socketpair."""

from __future__ import annotations

import os
import socket
import tempfile
import threading
import time
from dataclasses import dataclass, field

import pytest

from aird.app_context import AppContext
from aird.core.bulk.engine import BulkEngine
from aird.core.bulk.protocol import STATUS_OK, pack_client_header
from aird.core.bulk.ticket import BulkTicketClaims, mint_bulk_ticket
import aird.constants as constants


@dataclass
class _FakeAppContext(AppContext):
    db_conn: object = None
    services: dict = field(default_factory=dict)


def _run_put_server(engine: BulkEngine, server_sock: socket.socket) -> None:
    client, _addr = server_sock.accept()
    engine.handle_tcp_socket(client)


def test_bulk_put_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setattr(constants, "ROOT_DIR", str(tmp_path))
    monkeypatch.setattr(constants, "MULTI_USER", False)
    secret = "unit-test-secret"
    size = 256 * 1024
    payload = os.urandom(size)

    server_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server_sock.bind(("127.0.0.1", 0))
    server_sock.listen(1)
    host, port = server_sock.getsockname()

    engine = BulkEngine(_FakeAppContext(), secret)
    thread = threading.Thread(target=_run_put_server, args=(engine, server_sock), daemon=True)
    thread.start()

    claims = BulkTicketClaims(
        op="PUT",
        username="tester",
        size=size,
        exp=int(time.time()) + 120,
        upload_dir="",
        filename="bench.txt",
    )
    ticket = mint_bulk_ticket(secret, claims).encode("utf-8")

    client = socket.create_connection((host, port), timeout=10)
    try:
        client.sendall(pack_client_header(ticket))
        sent = 0
        while sent < size:
            n = client.send(payload[sent : sent + 65536])
            assert n > 0
            sent += n
        status = client.recv(1)
        assert status == bytes([STATUS_OK])
    finally:
        client.close()
        server_sock.close()
    thread.join(timeout=5)

    out = tmp_path / "bench.txt"
    assert out.is_file()
    assert out.stat().st_size == size
    assert out.read_bytes() == payload


def test_bulk_get_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setattr(constants, "ROOT_DIR", str(tmp_path))
    monkeypatch.setattr(constants, "MULTI_USER", False)
    secret = "unit-test-secret"
    payload = os.urandom(128 * 1024)
    rel = "dl.bin"
    (tmp_path / rel).write_bytes(payload)
    size = len(payload)

    server_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server_sock.bind(("127.0.0.1", 0))
    server_sock.listen(1)
    host, port = server_sock.getsockname()

    engine = BulkEngine(_FakeAppContext(), secret)
    thread = threading.Thread(target=_run_put_server, args=(engine, server_sock), daemon=True)
    thread.start()

    claims = BulkTicketClaims(
        op="GET",
        username="tester",
        size=size,
        exp=int(time.time()) + 120,
        relpath=rel,
    )
    ticket = mint_bulk_ticket(secret, claims).encode("utf-8")

    client = socket.create_connection((host, port), timeout=10)
    try:
        client.sendall(pack_client_header(ticket))
        received = b""
        while len(received) < size + 1:
            chunk = client.recv(min(65536, size + 1 - len(received)))
            assert chunk
            received += chunk
        file_data = received[:size]
        status = received[size : size + 1]
        assert status == bytes([STATUS_OK])
        assert file_data == payload
    finally:
        client.close()
        server_sock.close()
    thread.join(timeout=5)
