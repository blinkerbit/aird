"""Hosting-profile persistence and strategy regression tests."""

from __future__ import annotations

import copy
import sqlite3

import pytest

import aird.constants as constants
from aird.db import init_db
from aird.db.config import load_server_config
from aird.db.ranged_uploads import create_session, get_session
from aird.services.config_service import ConfigService


@pytest.fixture(autouse=True)
def _restore_runtime(monkeypatch):
    original_upload = copy.deepcopy(constants.UPLOAD_CONFIG)
    original_profile = constants.TRANSFER_PROFILE
    original_revision = constants.TRANSFER_CONFIG_REVISION
    monkeypatch.delenv("AIRD_TRANSFER_PROFILE", raising=False)
    yield
    monkeypatch.delenv("AIRD_TRANSFER_PROFILE", raising=False)
    constants.UPLOAD_CONFIG.clear()
    constants.UPLOAD_CONFIG.update(original_upload)
    constants.set_transfer_profile(original_profile, original_revision)


@pytest.fixture
def conn():
    db = sqlite3.connect(":memory:")
    init_db(db)
    yield db
    db.close()


def test_cloudflare_strategy_caps_requests_below_100_mb():
    constants.UPLOAD_CONFIG["single_request_max_mb"] = 200
    constants.UPLOAD_CONFIG["range_chunk_mb"] = 200
    constants.UPLOAD_CONFIG["range_upload_concurrency"] = 64
    constants.set_transfer_profile("cloudflare", 7)

    strategy = constants.get_effective_transfer_strategy()

    assert strategy["profile"] == "cloudflare"
    assert strategy["revision"] == 7
    assert strategy["directUploadMaxBytes"] == 90 * 1024 * 1024
    assert strategy["rangeChunkBytes"] == 90 * 1024 * 1024
    assert strategy["rangeUploadConcurrency"] == 8


def test_wireguard_strategy_is_single_stream():
    constants.set_transfer_profile("wireguard")
    constants.refresh_upload_derived_constants()

    strategy = constants.get_effective_transfer_strategy()

    assert strategy["uploadTransport"] == "stream"
    assert strategy["downloadTransport"] == "stream"
    assert strategy["rangeUploadConcurrency"] == 1
    assert constants.LARGE_FILE_THRESHOLD_BYTES > constants.MAX_FILE_SIZE


def test_environment_profile_overrides_persisted_value(conn, monkeypatch):
    service = ConfigService()
    service.save_transfer_profile(conn, "cloudflare")
    monkeypatch.setenv("AIRD_TRANSFER_PROFILE", "wireguard")

    runtime = service.get_runtime_config(conn)

    assert runtime["configuredProfile"] == "cloudflare"
    assert runtime["profile"] == "wireguard"
    assert runtime["environmentOverride"] is True


def test_profile_save_increments_shared_revision(conn):
    service = ConfigService()

    first = service.save_transfer_profile(conn, "cloudflare")
    second = service.save_transfer_profile(conn, "open")

    assert second["revision"] == first["revision"] + 1
    assert load_server_config(conn)["hosting_profile"] == "open"


def test_ranged_session_keeps_profile_snapshot(conn, tmp_path):
    temp_path = tmp_path / "upload.part"
    temp_path.touch()

    create_session(
        conn,
        session_id="snapshot",
        username="alice",
        upload_dir="",
        filename="large.bin",
        temp_path=str(temp_path),
        total_size=200,
        transfer_profile="cloudflare",
        chunk_bytes=90,
    )
    constants.set_transfer_profile("wireguard")

    session = get_session(conn, "snapshot")
    assert session is not None
    assert session["transfer_profile"] == "cloudflare"
    assert session["chunk_bytes"] == 90
