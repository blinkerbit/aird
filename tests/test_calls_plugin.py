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
