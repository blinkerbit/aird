"""Tests for secret_storage encryption."""

import os

from aird.core.secret_storage import decrypt_secret, encrypt_secret, _reset_fernet_cache


class TestSecretStorage:
    def test_roundtrip_with_key(self, monkeypatch):
        monkeypatch.setenv("AIRD_SECRETS_KEY", "test-key-material-for-unit-tests")
        _reset_fernet_cache()
        plain = "s3cret-pass"
        enc = encrypt_secret(plain)
        assert enc.startswith("enc:v1:")
        assert decrypt_secret(enc) == plain

    def test_plaintext_without_key(self, monkeypatch):
        monkeypatch.delenv("AIRD_SECRETS_KEY", raising=False)
        monkeypatch.delenv("AIRD_COOKIE_SECRET", raising=False)
        _reset_fernet_cache()
        plain = "local-dev-password"
        assert encrypt_secret(plain) == plain
        assert decrypt_secret(plain) == plain

    def test_legacy_plaintext_preserved(self, monkeypatch):
        monkeypatch.setenv("AIRD_SECRETS_KEY", "k")
        assert decrypt_secret("plaintext-stored") == "plaintext-stored"
