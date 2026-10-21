"""Chat events reach sockets that live in another worker."""

from __future__ import annotations

import json
import socket
import struct
import time

import pytest

from aird.plugins.chat.hub_bus import (
    connect_if_started,
    publish,
    start_listener,
    stop_listener,
)
from aird.plugins.chat.service import ChatHub

_HDR = struct.Struct(">I")


def _read_frame(sock: socket.socket) -> dict:
    header = b""
    while len(header) < 4:
        chunk = sock.recv(4 - len(header))
        if not chunk:
            raise ConnectionError("closed")
        header += chunk
    (size,) = _HDR.unpack(header)
    body = b""
    while len(body) < size:
        chunk = sock.recv(size - len(body))
        if not chunk:
            raise ConnectionError("closed")
        body += chunk
    return json.loads(body)


def _wait_until(predicate, timeout: float = 2.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.02)
    raise AssertionError("timed out")


@pytest.fixture
def hub_path(tmp_path):
    path = str(tmp_path / "chat-hub.sock")
    start_listener(path)
    try:
        yield path
    finally:
        stop_listener()


def test_fanout_skips_sender(hub_path):
    left = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    right = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    left.connect(hub_path)
    right.connect(hub_path)
    time.sleep(0.05)
    raw = json.dumps({"op": "deliver", "user_ids": [7], "payload": {"type": "chat_message"}}).encode()
    left.sendall(_HDR.pack(len(raw)) + raw)
    right.settimeout(2)
    frame = _read_frame(right)
    assert frame["payload"]["type"] == "chat_message"
    assert frame["user_ids"] == [7]
    left.settimeout(0.2)
    with pytest.raises(socket.timeout):
        _read_frame(left)
    left.close()
    right.close()


def test_hub_reaches_peer_worker(hub_path):
    peer = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    peer.connect(hub_path)
    peer.settimeout(2)

    class Socket:
        def __init__(self):
            self.raw = []

        def write_message(self, raw):
            self.raw.append(raw)

    hub = ChatHub()
    connect_if_started(hub.handle_bus_frame, on_connect=hub.publish_presence)
    _wait_until(lambda: publish({"op": "warmup"}) is True)
    while True:
        frame = _read_frame(peer)
        if frame.get("op") == "warmup":
            break

    local = Socket()
    hub.add(1, local, "alice")
    hub.send_to_user(1, {"type": "chat_message", "id": "local"})
    hub.send_to_user(2, {"type": "chat_message", "id": "remote"})

    assert json.loads(local.raw[-1])["id"] == "local"
    seen = set()
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline and "remote" not in seen:
        frame = _read_frame(peer)
        if frame.get("op") == "deliver":
            seen.add(frame["payload"]["id"])
    assert "local" in seen
    assert "remote" in seen
    peer.close()
