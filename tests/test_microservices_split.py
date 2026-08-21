import time

from aird.ms import jwt_auth
from aird.ms.admin_service import routes as admin_routes
from aird.ms.auth_service import routes as auth_routes
from aird.ms.browse_service import routes as browse_routes
from aird.ms import common as ms_common
from aird.ms.p2p_service import routes as p2p_routes
from aird.ms.search_service import routes as search_routes
from aird.ms.shares_service import routes as shares_routes
from aird.ms.service_runtime import route_path


def _paths(routes):
    return {route_path(route) for route in routes}


def test_feature_routes_have_single_service_owner():
    assert "/auth/verify" in _paths(auth_routes)
    assert "/files/{path:path}" in _paths(browse_routes)
    assert "/search/ws" in _paths(search_routes)
    assert "/shared/{token}" in _paths(shares_routes)
    assert "/shared/{token}/verify" in _paths(shares_routes)
    assert "/shared/{token}/file/{path:path}" in _paths(shares_routes)
    assert "/api/share/details_by_id" in _paths(shares_routes)
    assert "/api/users/search" in _paths(shares_routes)
    assert "/p2p/signal" in _paths(p2p_routes)
    assert "/admin" in _paths(admin_routes)
    assert "/admin/users/create" in _paths(admin_routes)
    assert "/api/download/zip" in _paths(browse_routes)

    assert "/search/ws" not in _paths(browse_routes)
    assert "/p2p/signal" not in _paths(browse_routes)
    assert "/admin" not in _paths(browse_routes)


def test_mesh_secret_is_fail_closed(monkeypatch):
    monkeypatch.delenv("AIRD_MESH_SECRET", raising=False)
    monkeypatch.delenv("AIRD_DEV_OPEN_AUTH", raising=False)
    monkeypatch.delenv("AIRD_ALLOW_INSECURE_MESH", raising=False)
    assert ms_common.mesh_secret_ok({"x-aird-mesh": "anything"}) is False
    try:
        ms_common.require_mesh_configuration("aird-test")
        assert False, "expected SystemExit"
    except SystemExit:
        pass

    monkeypatch.setenv("AIRD_MESH_SECRET", "mesh-secret")
    assert ms_common.mesh_secret_ok({"x-aird-mesh": "mesh-secret"}) is True
    assert ms_common.mesh_secret_ok({"x-aird-mesh": "wrong"}) is False


def test_jwt_round_trip_and_type_separation(monkeypatch):
    monkeypatch.setenv("AIRD_JWT_SECRET", "test-secret-with-sufficient-entropy")
    settings = {}
    access = jwt_auth.encode_token(
        settings,
        username="alice",
        role="admin",
        token_type="access",
        ttl=60,
    )
    payload = jwt_auth.decode_token(settings, access)
    assert payload
    assert payload["sub"] == "alice"
    assert payload["role"] == "admin"
    assert jwt_auth.decode_token(settings, access, expected_type="refresh") is None


def test_jwt_rejects_tampering_and_expiry(monkeypatch):
    monkeypatch.setenv("AIRD_JWT_SECRET", "test-secret-with-sufficient-entropy")
    settings = {}
    expired = jwt_auth.encode_token(
        settings,
        username="alice",
        role="user",
        token_type="access",
        ttl=-1,
    )
    assert jwt_auth.decode_token(settings, expired) is None

    valid = jwt_auth.encode_token(
        settings,
        username="alice",
        role="user",
        token_type="access",
        ttl=60,
    )
    replacement = "A" if valid[-1] != "A" else "B"
    assert jwt_auth.decode_token(settings, valid[:-1] + replacement) is None
    assert int(time.time()) > 0
