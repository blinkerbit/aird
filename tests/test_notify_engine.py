"""Socket first, Web Push after the confirm window, retry until expire."""

from aird.notifications.engine import deliver, drop_pending_calls, flush_due
import aird.notifications.engine as engine


class _Hub:
    def __init__(self):
        self.sent = []
        self.online = False

    def send_to_user(self, user_id, payload):
        self.sent.append((user_id, payload))

    def is_online(self, user_id):
        return self.online


def _setup(monkeypatch, tmp_path, hub):
    monkeypatch.setattr("aird.constants.DB_PATH", str(tmp_path / "n.sqlite3"))
    monkeypatch.setattr("aird.plugins.chat.service.get_chat_hub", lambda: hub)
    monkeypatch.setattr(engine, "_kick", lambda delay: None)
    clock = {"t": 1_000_000.0}
    monkeypatch.setattr(engine.time, "time", lambda: clock["t"])
    sent = []

    def fake_push(conn, username, payload, ttl=86400):
        sent.append((username, dict(payload), ttl))
        return 1

    monkeypatch.setattr("aird.pwa.push.send_web_push", fake_push)
    return clock, sent


def test_offline_push_is_immediate_and_once(monkeypatch, tmp_path):
    hub = _Hub()
    clock, sent = _setup(monkeypatch, tmp_path, hub)
    deliver(
        username="usera",
        user_id=2,
        kind="call",
        socket_payload={"type": "call_invite", "conversation_id": "c1"},
        push_payload={"title": "Voice call", "call_id": "k1"},
        expire_s=25,
        push_ttl=30,
    )
    assert hub.sent[0][1]["notify_id"]
    assert sent == []
    assert flush_due() == 1
    assert sent[0][0] == "usera"
    assert sent[0][1]["kind"] == "call"
    assert sent[0][1]["notify_id"] == hub.sent[0][1]["notify_id"]
    assert sent[0][2] == 30
    assert flush_due() == 0
    assert len(sent) == 1
    assert clock["t"] == 1_000_000.0


def test_online_pushes_immediately(monkeypatch, tmp_path):
    hub = _Hub()
    hub.online = True
    clock, sent = _setup(monkeypatch, tmp_path, hub)
    deliver(
        username="usera",
        user_id=2,
        kind="message",
        socket_payload={"type": "chat_notify"},
        push_payload={"title": "vis"},
        expire_s=86400,
        push_ttl=86400,
    )
    assert flush_due() == 1
    assert len(sent) == 1
    assert sent[0][2] == 86400
    assert clock["t"] == 1_000_000.0


def test_expired_call_is_not_pushed(monkeypatch, tmp_path):
    hub = _Hub()
    hub.online = True
    clock, sent = _setup(monkeypatch, tmp_path, hub)
    deliver(
        username="usera",
        user_id=2,
        kind="call",
        socket_payload={"type": "call_invite"},
        push_payload={"title": "Voice call"},
        expire_s=25,
        push_ttl=30,
    )
    clock["t"] += 26
    assert flush_due() == 0
    assert sent == []


def test_ended_call_is_not_pushed(monkeypatch, tmp_path):
    hub = _Hub()
    clock, sent = _setup(monkeypatch, tmp_path, hub)
    deliver(
        username="usera",
        user_id=2,
        kind="call",
        socket_payload={"type": "call_invite", "conversation_id": "c1"},
        push_payload={"title": "Voice call", "call_id": "k1", "conversation_id": "c1"},
        expire_s=25,
        push_ttl=30,
    )
    drop_pending_calls("c1", "k1")
    assert flush_due() == 0
    assert sent == []
    assert clock["t"] == 1_000_000.0


def test_failed_push_retries(monkeypatch, tmp_path):
    hub = _Hub()
    clock, sent = _setup(monkeypatch, tmp_path, hub)
    calls = {"n": 0}

    def flaky(conn, username, payload, ttl=86400):
        calls["n"] += 1
        sent.append(payload["notify_id"])
        return 0 if calls["n"] == 1 else 1

    monkeypatch.setattr("aird.pwa.push.send_web_push", flaky)
    deliver(
        username="usera",
        user_id=2,
        kind="message",
        socket_payload={"type": "chat_notify"},
        push_payload={"title": "vis"},
        expire_s=86400,
        push_ttl=86400,
    )
    assert flush_due() == 1
    assert flush_due() == 0
    clock["t"] += 1
    assert flush_due() == 1
    assert calls["n"] == 2
    assert flush_due() == 0
