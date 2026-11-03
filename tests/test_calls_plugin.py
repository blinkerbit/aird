"""Voice/video calls plugin (LiveKit token)."""

from aird.plugins.calls.config import livekit_configured, room_name
from aird.plugins.calls.token import create_participant_token


def test_livekit_token_shape():
    token = create_participant_token(
        api_key="testkey",
        api_secret="secret",
        room="aird-conv1",
        identity="alice",
        name="Alice",
        ttl_seconds=120,
    )
    assert len(token.split(".")) == 3


def test_room_name_sanitized():
    assert room_name("abc-123").startswith("aird-")


def test_livekit_configured_false_without_env(monkeypatch):
    monkeypatch.delenv("LIVEKIT_URL", raising=False)
    monkeypatch.delenv("LIVEKIT_API_KEY", raising=False)
    monkeypatch.delenv("LIVEKIT_API_SECRET", raising=False)
    assert livekit_configured() is False


import aird.plugins.calls.notify as call_notify


class _Hub:
    def __init__(self):
        self.sent = []

    def broadcast_user_ids(self, ids, payload, exclude_user_id=None):
        self.sent.append((list(ids), dict(payload), exclude_user_id))


def _call_env(monkeypatch, tmp_path):
    monkeypatch.setattr("aird.constants.DB_PATH", str(tmp_path / "calls.sqlite3"))
    hub = _Hub()
    monkeypatch.setattr(call_notify, "get_chat_hub", lambda: hub)
    monkeypatch.setattr(call_notify, "_member_ids", lambda username, conv: [1, 2])
    monkeypatch.setattr(call_notify, "clear_call_rings", lambda *a, **k: None)
    monkeypatch.setattr(call_notify, "_stop_remote_rings", lambda *a, **k: None)
    monkeypatch.setattr(call_notify, "drop_pending_calls", lambda *a, **k: None)
    return hub


def test_unanswered_call_becomes_missed_once(monkeypatch, tmp_path):
    hub = _call_env(monkeypatch, tmp_path)
    call_notify._record_call("k1", "c1", "alice")
    call_notify._expire_ring("alice", "c1", "k1")
    call_notify._expire_ring("alice", "c1", "k1")
    assert [p["type"] for _, p, _ in hub.sent] == ["call_missed"]
    assert hub.sent[0][1]["call_id"] == "k1"


def test_answered_call_is_not_missed(monkeypatch, tmp_path):
    hub = _call_env(monkeypatch, tmp_path)
    call_notify._record_call("k2", "c1", "alice")
    call_notify.dispatch_call_signal(
        username="bob",
        conversation_id="c1",
        actor_id=2,
        signal_type="call_accept",
        extra={"call_id": "k2"},
    )
    hub.sent.clear()
    call_notify._expire_ring("alice", "c1", "k2")
    assert hub.sent == []


def test_accept_tells_callers_other_devices_to_stop(monkeypatch, tmp_path):
    hub = _call_env(monkeypatch, tmp_path)
    call_notify.dispatch_call_signal(
        username="bob",
        conversation_id="c1",
        actor_id=2,
        signal_type="call_accept",
        extra={"call_id": "k3"},
    )
    (ids_a, accept, excluded), (ids_b, answered, _) = hub.sent
    assert accept["type"] == "call_accept" and excluded == 2
    assert answered["type"] == "call_answered" and ids_b == [2]
    assert answered["call_id"] == "k3"


def test_ended_call_is_not_missed(monkeypatch, tmp_path):
    hub = _call_env(monkeypatch, tmp_path)
    call_notify._record_call("k4", "c1", "alice")
    call_notify.dispatch_call_signal(
        username="alice", conversation_id="c1", actor_id=1, signal_type="call_end", extra={"call_id": "k4"}
    )
    hub.sent.clear()
    call_notify._expire_ring("alice", "c1", "k4")
    assert hub.sent == []


def test_peek_missed_for_callee_not_caller(monkeypatch, tmp_path):
    _call_env(monkeypatch, tmp_path)
    call_notify._record_call("k5", "c1", "alice")
    call_notify._settle_call("c1", "k5", "missed")
    monkeypatch.setattr(
        call_notify.chat_db,
        "members_of",
        lambda caller, conv: [{"username": "alice"}, {"username": "bob"}],
    )
    missed = call_notify.peek_missed("bob")
    assert missed["call_id"] == "k5"
    assert missed["type"] == "call_missed"
    assert call_notify.peek_missed("alice") is None


def test_answered_call_can_be_reminted(monkeypatch, tmp_path):
    _call_env(monkeypatch, tmp_path)
    call_notify._record_call("k7", "c1", "alice")
    call_notify._settle_call("c1", "k7", "answered")
    assert call_notify.call_status("k7") == "answered"
    assert call_notify.call_status("k7") not in ("ended", "missed")


def test_call_status_tracks_lifecycle(monkeypatch, tmp_path):
    _call_env(monkeypatch, tmp_path)
    call_notify._record_call("k6", "c1", "alice")
    assert call_notify.call_status("k6") == "ringing"
    call_notify._settle_call("c1", "k6", "ended")
    assert call_notify.call_status("k6") == "ended"
    assert call_notify.call_status("missing") == ""
