"""LDAPS connections require certificate validation."""

import ssl

from aird.core.ldap_server import make_ldap_server


def test_ldaps_requires_certificate_validation(monkeypatch):
    monkeypatch.delenv("AIRD_LDAP_CA_FILE", raising=False)
    server = make_ldap_server("ldaps://directory.example.com")
    assert server.ssl is True
    assert server.tls.validate == ssl.CERT_REQUIRED


def test_ldap_stays_cleartext():
    server = make_ldap_server("ldap://directory.example.com")
    assert server.ssl is False
