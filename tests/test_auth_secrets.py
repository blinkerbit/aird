"""Tests for aird.core.auth_secrets."""

import os
import sys
import tempfile

import pytest

from aird.core.auth_secrets import (
    hash_auth_secret,
    load_or_create_secret_file,
    normalize_stored_secret,
    resolve_auth_secret,
    secrets_dir_for_root,
    verify_auth_secret,
)


class TestVerifyAuthSecret:
    def test_plain_match(self):
        assert verify_auth_secret("my-token", "my-token") is True

    def test_plain_mismatch(self):
        assert verify_auth_secret("wrong", "my-token") is False

    def test_hashed_match(self):
        stored = hash_auth_secret("my-token")
        assert verify_auth_secret("my-token", stored) is True

    def test_hashed_mismatch(self):
        stored = hash_auth_secret("my-token")
        assert verify_auth_secret("other", stored) is False

    def test_empty_rejected(self):
        assert verify_auth_secret("", "x") is False
        assert verify_auth_secret("x", None) is False


class TestNormalizeStoredSecret:
    def test_bare_hex_becomes_prefixed(self):
        hex64 = "a" * 64
        assert normalize_stored_secret(hex64) == f"sha256:{hex64}"

    def test_plain_unchanged(self):
        assert normalize_stored_secret("plain-token") == "plain-token"


class TestResolveAuthSecret:
    def test_cli_wins(self):
        with tempfile.TemporaryDirectory() as tmp:
            secrets_dir = secrets_dir_for_root(tmp)
            value, explicit = resolve_auth_secret(
                cli_value="cli-tok",
                config_value="cfg-tok",
                env_value="env-tok",
                secrets_dir=secrets_dir,
                secret_filename="access_token",
                allow_auto_generate=True,
            )
            assert value == "cli-tok"
            assert explicit is True

    def test_hashed_config(self):
        with tempfile.TemporaryDirectory() as tmp:
            secrets_dir = secrets_dir_for_root(tmp)
            stored = hash_auth_secret("secret")
            value, explicit = resolve_auth_secret(
                cli_value=None,
                config_value=stored,
                env_value=None,
                secrets_dir=secrets_dir,
                secret_filename="access_token",
                allow_auto_generate=True,
            )
            assert value == stored
            assert explicit is True
            assert verify_auth_secret("secret", value)

    def test_persists_to_secrets_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            secrets_dir = secrets_dir_for_root(tmp)
            value1, explicit1 = resolve_auth_secret(
                cli_value=None,
                config_value=None,
                env_value=None,
                secrets_dir=secrets_dir,
                secret_filename="access_token",
                allow_auto_generate=True,
            )
            assert value1
            assert explicit1 is False
            value2, explicit2 = resolve_auth_secret(
                cli_value=None,
                config_value=None,
                env_value=None,
                secrets_dir=secrets_dir,
                secret_filename="access_token",
                allow_auto_generate=True,
            )
            assert value2 == value1
            assert explicit2 is True

    def test_multi_user_blocks_auto_generate(self):
        with tempfile.TemporaryDirectory() as tmp:
            secrets_dir = secrets_dir_for_root(tmp)
            value, explicit = resolve_auth_secret(
                cli_value=None,
                config_value=None,
                env_value=None,
                secrets_dir=secrets_dir,
                secret_filename="access_token",
                allow_auto_generate=False,
            )
            assert value is None
            assert explicit is False


class TestLoadOrCreateSecretFile:
    @pytest.mark.skipif(sys.platform == "win32", reason="Unix file modes")
    def test_file_mode_restricted(self):
        with tempfile.TemporaryDirectory() as tmp:
            secrets_dir = secrets_dir_for_root(tmp)
            path = secrets_dir / "test_secret"
            load_or_create_secret_file(secrets_dir, "test_secret")
            mode = path.stat().st_mode & 0o777
            assert mode == 0o600
