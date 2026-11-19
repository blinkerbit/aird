import base64

from aird.core.android_facet import (
    apk_key_hash_origin,
    assetlinks_document,
    merge_android_origins,
    normalize_fingerprint,
)
from aird.db.schema import init_db
from aird.db.webauthn import add_android_facet, list_android_facets
from aird.handlers.webauthn_handlers import extend_webauthn_origins


def _conn():
    import sqlite3

    conn = sqlite3.connect(":memory:")
    init_db(conn)
    return conn


def test_normalize_fingerprint_and_origin():
    raw = bytes(range(32))
    compact = raw.hex()
    fingerprint = normalize_fingerprint(compact)
    assert fingerprint == ":".join(compact[i : i + 2].upper() for i in range(0, 64, 2))
    origin = apk_key_hash_origin(fingerprint)
    token = base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")
    assert origin == f"android:apk-key-hash:{token}"
    assert normalize_fingerprint("zz") is None
    assert normalize_fingerprint("") is None


def test_assetlinks_document():
    fingerprint = normalize_fingerprint("ab" * 32)
    document = assetlinks_document([fingerprint, fingerprint.lower()])
    assert document[0]["target"]["package_name"] == "com.aird"
    assert document[0]["target"]["sha256_cert_fingerprints"] == [fingerprint]
    assert "delegate_permission/common.get_login_creds" in document[0]["relation"]
    assert assetlinks_document([]) == []


def test_facet_roundtrip_extends_origins():
    conn = _conn()
    fingerprint = normalize_fingerprint("cd" * 32)
    assert add_android_facet(conn, fingerprint) is True
    assert add_android_facet(conn, "not-a-cert") is False
    assert list_android_facets(conn) == [fingerprint]
    origins = extend_webauthn_origins(conn, ["https://files.example"])
    assert origins[0] == "https://files.example"
    assert apk_key_hash_origin(fingerprint) in origins
    assert merge_android_origins(["https://files.example"], []) == ["https://files.example"]
    conn.close()
