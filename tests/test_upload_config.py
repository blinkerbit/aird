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
    yield
    constants.UPLOAD_CONFIG.clear()
    constants.UPLOAD_CONFIG.update(orig)
    constants.MAX_FILE_SIZE = orig_max
    constants.LARGE_FILE_THRESHOLD_BYTES = orig_threshold


def test_default_upload_config():
    assert constants.UPLOAD_CONFIG["max_file_size_mb"] == 10240


def test_stream_upload_always_allows_full_file_size():
    constants.UPLOAD_CONFIG["max_file_size_mb"] = 2048
    constants.refresh_upload_derived_constants()
    assert constants.LARGE_FILE_THRESHOLD_BYTES == constants.MAX_FILE_SIZE + 1


def test_merge_persisted_upload_config():
    constants.merge_persisted_upload_config({"max_file_size_mb": 512})
    assert constants.UPLOAD_CONFIG["max_file_size_mb"] == 512
    assert constants.MAX_FILE_SIZE == 512 * 1024 * 1024


def test_upload_request_max_body_covers_max_file():
    constants.merge_persisted_upload_config({"max_file_size_mb": 90})
    assert constants.UPLOAD_REQUEST_MAX_BODY_SIZE >= constants.MAX_FILE_SIZE
