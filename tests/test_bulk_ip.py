"""Tests for bulk client IP normalization."""

from __future__ import annotations

from aird.core.bulk.ip import client_ips_match, normalize_client_ip


def test_normalize_ipv6_loopback():
    assert normalize_client_ip("::1") == "::1"
    assert normalize_client_ip("::1:54321") == "::1"


def test_normalize_ipv4_loopback():
    assert normalize_client_ip("127.0.0.1") == "127.0.0.1"
    assert normalize_client_ip("127.0.0.1:8080") == "127.0.0.1"


def test_loopback_equivalence():
    assert client_ips_match("::1", "::1")
    assert client_ips_match("::1", "127.0.0.1")
    assert client_ips_match("127.0.0.1", "::1:9000")


def test_mismatch_real_ips():
    assert not client_ips_match("192.168.1.10", "192.168.1.11")
