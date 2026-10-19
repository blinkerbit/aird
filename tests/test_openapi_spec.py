"""The OpenAPI document matches the routes a native client will call."""

from aird.openapi_spec import openapi_document


def test_document_is_openapi3_and_covers_the_native_contract():
    doc = openapi_document()
    assert doc["openapi"] == "3.0.3"
    paths = doc["paths"]
    for path in (
        "/api/auth/login",
        "/health",
        "/api/openapi.json",
        "/api/docs",
        "/api/files/{path}",
        "/files/{path}",
        "/upload",
        "/api/upload/range/session",
        "/api/upload/range/{upload_id}",
        "/api/upload/range/{upload_id}/status",
        "/mkdir",
        "/delete",
        "/share/create",
        "/share/list",
        "/api/features",
        "/api/bulk/ticket",
        "/search/ws",
        "/api/chat/conversations",
        "/api/chat/conversations/{conversation_id}/messages",
        "/api/chat/e2e/keys",
        "/ws/chat",
    ):
        assert path in paths
    assert "gitlab" not in "".join(paths)
    assert "onedrive" not in "".join(paths)
    assert paths["/api/chat/conversations"]["post"]["security"] == [
        {"bearerAuth": []},
        {"sessionCookie": []},
    ]
    assert paths["/api/auth/login"]["post"]["security"] == []
    session = paths["/api/upload/range/session"]["post"]
    assert session["requestBody"]["content"]["application/json"]["schema"]["required"] == [
        "filename",
        "total_size",
    ]
    assert paths["/health"]["get"]["security"] == []
