"""Tests for upload config defaults and derived constants."""

from __future__ import annotations

import copy

import pytest

import aird.constants as constants


@pytest.fixture(autouse=True)
def _restore_upload_config():
    orig = copy.deepcopy(constants.UPLOAD_CONFIG)
    orig_max = constants.MAX_FILE_SIZE
    orig_threshold = constants.LARGE_FILE_THRESHOLD_BYTES
    orig_chunk = constants.RANGE_CHUNK_BYTES
    orig_conc = constants.RANGE_UPLOAD_CONCURRENCY
    orig_ws = constants.WS_CHUNK_BYTES
    yield
    constants.UPLOAD_CONFIG.clear()
    constants.UPLOAD_CONFIG.update(orig)
    constants.MAX_FILE_SIZE = orig_max
    constants.LARGE_FILE_THRESHOLD_BYTES = orig_threshold
    constants.RANGE_CHUNK_BYTES = orig_chunk
    constants.RANGE_UPLOAD_CONCURRENCY = orig_conc
    constants.WS_CHUNK_BYTES = orig_ws


def test_default_upload_config_includes_parallel_http_settings():
    assert constants.UPLOAD_CONFIG["max_file_size_mb"] == 10240
    assert constants.UPLOAD_CONFIG["single_request_max_mb"] == 100
    assert constants.UPLOAD_CONFIG["range_chunk_mb"] == 90
    assert constants.UPLOAD_CONFIG["range_upload_concurrency"] == 16
    assert constants.UPLOAD_CONFIG["ws_chunk_mb"] == 90


def test_single_request_max_zero_uses_proxy_safe_threshold():
    constants.UPLOAD_CONFIG["max_file_size_mb"] = 10240
    constants.UPLOAD_CONFIG["single_request_max_mb"] = 0
    constants.refresh_upload_derived_constants()
    assert constants.LARGE_FILE_THRESHOLD_BYTES == 100 * 1024 * 1024


def test_merge_persisted_upload_config_applies_chunk_settings():
    constants.merge_persisted_upload_config(
        {
            "max_file_size_mb": 2048,
            "single_request_max_mb": 50,
            "range_chunk_mb": 32,
            "range_upload_concurrency": 8,
            "ws_chunk_mb": 16,
        }
    )
    assert constants.UPLOAD_CONFIG["max_file_size_mb"] == 2048
    assert constants.UPLOAD_CONFIG["single_request_max_mb"] == 50
    assert constants.RANGE_CHUNK_BYTES == 32 * 1024 * 1024
    assert constants.RANGE_UPLOAD_CONCURRENCY == 8
    assert constants.WS_CHUNK_BYTES == 16 * 1024 * 1024
    assert constants.LARGE_FILE_THRESHOLD_BYTES == 50 * 1024 * 1024


def test_merge_persisted_empty_dict_seeds_single_request_default():
    constants.merge_persisted_upload_config({})
    assert constants.UPLOAD_CONFIG["single_request_max_mb"] == 100
    assert constants.UPLOAD_CONFIG["range_chunk_mb"] == 90


def test_upload_request_max_body_covers_chunk_size():
    constants.merge_persisted_upload_config({"range_chunk_mb": 90})
    chunk_plus_margin = constants.RANGE_CHUNK_BYTES + (2 * 1024 * 1024)
    assert constants.UPLOAD_REQUEST_MAX_BODY_SIZE >= chunk_plus_margin
