"""Enhanced Secure Chat stays in memory and never touches the mailbox."""

from __future__ import annotations

import base64

import pytest

from aird.plugins.chat.secret import EnhancedSecureHub


def _jwk(seed: int) -> dict:
    raw = bytes([seed]) * 32
    coord = base64.urlsafe_b64encode(raw).decode().rstrip("=")
    return {"kty": "EC", "crv": "P-256", "x": coord, "y": coord}


def _blob() -> dict:
    iv = base64.b64encode(b"123456789012").decode()
    ct = base64.b64encode(b"ciphertext-bytes").decode()
    return {"v": 1, "iv": iv, "ct": ct}


def test_enhanced_secure_room_relays_without_storing_plaintext():
    hub = EnhancedSecureHub()
    room = hub.invite("alice", "bob")
    room = hub.join("alice", room["room_id"], _jwk(1))
    assert room["ready"] is False
    with pytest.raises(ValueError):
        hub.relay("alice", room["room_id"], _blob())
    room = hub.join("bob", room["room_id"], _jwk(2))
    assert room["ready"] is True
    peer, blob = hub.relay("alice", room["room_id"], _blob())
    assert peer == "bob"
    assert blob["ct"]
    assert hub.peer_for("bob", room["room_id"]) == "alice"
    pending = hub.pending_for("bob")
    assert pending[0]["type"] == "enhanced_open"
    assert "ct" not in pending[0]


def test_enhanced_secure_close_and_drop_remove_the_room():
    hub = EnhancedSecureHub()
    room = hub.invite("alice", "bob")
    hub.join("alice", room["room_id"], _jwk(3))
    closed = hub.close("alice", room["room_id"])
    assert closed["peer"] == "bob"
    with pytest.raises(ValueError):
        hub.join("bob", room["room_id"], _jwk(4))

    again = hub.invite("alice", "bob")
    hub.join("alice", again["room_id"], _jwk(5))
    ended = hub.drop_user("alice")
    assert ended == [(again["room_id"], "bob")]
    assert hub.pending_for("bob") == []


def test_enhanced_secure_rejects_private_key_material():
    hub = EnhancedSecureHub()
    room = hub.invite("alice", "bob")
    epk = _jwk(6)
    epk["d"] = "secret"
    with pytest.raises(ValueError):
        hub.join("alice", room["room_id"], epk)


def test_enhanced_secure_invite_reuses_the_same_pair():
    hub = EnhancedSecureHub()
    first = hub.invite("alice", "bob")
    second = hub.invite("bob", "alice")
    assert first["room_id"] == second["room_id"]
    with pytest.raises(ValueError):
        hub.invite("alice", "alice")
