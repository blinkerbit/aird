"""OpenAPI document for the HTTP API the web UI and native clients share."""

from __future__ import annotations

from typing import Any


def openapi_document() -> dict[str, Any]:
    """Return the OpenAPI 3 document served at ``/api/openapi.json``."""
    return {
        "openapi": "3.0.3",
        "info": {
            "title": "Aird API",
            "version": "0.5.6",
            "description": _DESCRIPTION,
        },
        "servers": [{"url": "/"}],
        "tags": [
            {"name": "meta", "description": "Health and this document."},
            {"name": "auth", "description": "Session login and bearer access."},
            {"name": "files", "description": "Browse, download, and mutate the file tree."},
            {"name": "transfers", "description": "Uploads, ranged uploads, zip, and bulk tickets."},
            {"name": "shares", "description": "Share links."},
            {"name": "account", "description": "Favorites, tags, sessions, feature flags."},
            {"name": "chat", "description": "Direct messages. Needs aird[chat], the direct_messages flag, and a user session."},
            {"name": "realtime", "description": "WebSocket endpoints used by the browser."},
        ],
        "components": _components(),
        "security": [{"bearerAuth": []}, {"sessionCookie": []}],
        "paths": _paths(),
    }


_DESCRIPTION = """
HTTP contract for Aird. The browser and a future native client (Android) call the same routes on this process. HTML pages are the current frontend; they are not a second API.

## Authentication

Send one of:

- `Authorization: Bearer <access token>` — the server access token (`--token` / `AIRD_ACCESS_TOKEN`). This is the path a native client should use.
- Session cookies `aird_session` and `user`, set by `POST /login`.

`xsrf_cookies` is on for cookie sessions. Those `POST`, `PUT`, and `DELETE` requests need the `_xsrf` cookie and the same value in `X-XSRFToken`. A request authenticated with `Authorization: Bearer` does not send that header. Use `POST /api/auth/login` to obtain a bearer session token for a user account.

File mutations redirect unless the request sends `Accept: application/json`. Native clients should send that header and read the JSON body.

## Chat

`/api/chat` and `/ws/chat` need `pip install "aird[chat]"`, the `direct_messages` flag, and a user session from `POST /api/auth/login` or `POST /login`. The server access token is not a chat user.

## Not in this contract yet

Admin, GitLab, and OneDrive stay on the web UI. Rate limiting at a reverse proxy is separate and not part of this API.
""".strip()


def _components() -> dict[str, Any]:
    return {
        "securitySchemes": {
            "bearerAuth": {
                "type": "http",
                "scheme": "bearer",
                "description": "Server access token.",
            },
            "sessionCookie": {
                "type": "apiKey",
                "in": "cookie",
                "name": "aird_session",
                "description": "Browser session. `user` is set alongside it.",
            },
        },
        "parameters": {
            "xsrf": {
                "name": "X-XSRFToken",
                "in": "header",
                "required": False,
                "schema": {"type": "string"},
                "description": "Must match the `_xsrf` cookie for a cookie session. Omit when Authorization Bearer authenticates.",
            },
            "acceptJson": {
                "name": "Accept",
                "in": "header",
                "required": True,
                "schema": {"type": "string", "enum": ["application/json"]},
                "description": "Without this header, file mutations respond with a redirect.",
            },
        },
        "schemas": {
            "ErrorText": {"type": "string"},
            "ErrorObject": {
                "type": "object",
                "properties": {"error": {"type": "string"}},
            },
            "FileEntry": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "is_dir": {"type": "boolean"},
                    "child_count": {"type": "integer", "nullable": True},
                    "size_bytes": {"type": "integer"},
                    "size_str": {"type": "string"},
                    "modified": {"type": "string"},
                    "modified_timestamp": {"type": "integer"},
                },
            },
            "FileList": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "files": {"type": "array", "items": {"$ref": "#/components/schemas/FileEntry"}},
                    "is_video": {"type": "boolean"},
                    "is_audio": {"type": "boolean"},
                },
            },
            "Health": {
                "type": "object",
                "required": ["status", "db"],
                "properties": {
                    "status": {"type": "string", "enum": ["ok", "error"]},
                    "db": {"type": "string", "enum": ["ok", "error", "not_configured"]},
                },
            },
            "MutationOk": {
                "type": "object",
                "properties": {
                    "ok": {"type": "boolean"},
                    "path": {"type": "string"},
                },
            },
            "RangeSession": {
                "type": "object",
                "properties": {
                    "upload_id": {"type": "string"},
                    "total_size": {"type": "integer"},
                    "chunk_bytes": {"type": "integer"},
                    "transfer_profile": {"type": "string"},
                    "resumed": {"type": "boolean"},
                },
            },
            "ShareCreate": {
                "type": "object",
                "properties": {
                    "paths": {"type": "array", "items": {"type": "string"}},
                    "allowed_users": {"type": "array", "items": {"type": "string"}},
                    "modify_users": {"type": "array", "items": {"type": "string"}},
                    "share_type": {"type": "string", "enum": ["static", "tag"]},
                    "allow_list": {"type": "array", "items": {"type": "string"}},
                    "avoid_list": {"type": "array", "items": {"type": "string"}},
                    "disable_token": {"type": "boolean"},
                    "expiry_date": {"type": "string", "nullable": True},
                    "tag_name": {"type": "string", "nullable": True},
                },
            },
        },
    }


def _json(schema: dict[str, Any] | str, *, status: str = "200", description: str = "OK") -> dict[str, Any]:
    body = {"$ref": schema} if isinstance(schema, str) else schema
    return {
        status: {
            "description": description,
            "content": {"application/json": {"schema": body}},
        }
    }


def _err() -> dict[str, Any]:
    return {
        "400": {"description": "Bad request", "content": {"text/plain": {"schema": {"$ref": "#/components/schemas/ErrorText"}}}},
        "401": {"description": "Not authenticated"},
        "403": {"description": "Denied"},
        "404": {"description": "Not found"},
    }


def _write_params() -> list[dict[str, Any]]:
    return [
        {"$ref": "#/components/parameters/xsrf"},
        {"$ref": "#/components/parameters/acceptJson"},
    ]


def _form(properties: dict[str, Any], required: list[str]) -> dict[str, Any]:
    return {
        "required": True,
        "content": {
            "application/x-www-form-urlencoded": {
                "schema": {
                    "type": "object",
                    "required": required,
                    "properties": properties,
                }
            }
        },
    }


def _op(
    tag: str,
    summary: str,
    responses: dict[str, Any],
    *,
    description: str = "",
    params: list[dict[str, Any]] | None = None,
    body: dict[str, Any] | None = None,
    auth: bool = True,
    security: list[dict[str, list]] | None = None,
) -> dict[str, Any]:
    operation: dict[str, Any] = {
        "tags": [tag],
        "summary": summary,
        "responses": responses,
    }
    if description:
        operation["description"] = description
    if params:
        operation["parameters"] = params
    if body is not None:
        operation["requestBody"] = body
    if security is not None:
        operation["security"] = security
    elif not auth:
        operation["security"] = []
    return operation


def _paths() -> dict[str, Any]:
    path_param = {
        "name": "path",
        "in": "path",
        "required": True,
        "schema": {"type": "string"},
        "description": "Path relative to the user root. Empty lists the root.",
    }
    upload_id = {
        "name": "upload_id",
        "in": "path",
        "required": True,
        "schema": {"type": "string"},
    }
    return {
        "/api/openapi.json": {
            "get": _op(
                "meta",
                "OpenAPI document",
                _json({"type": "object"}),
                auth=False,
            )
        },
        "/api/docs": {
            "get": _op(
                "meta",
                "Swagger UI",
                {"200": {"description": "HTML", "content": {"text/html": {"schema": {"type": "string"}}}}},
                auth=False,
            )
        },
        "/health": {
            "get": _op(
                "meta",
                "Process health",
                {
                    **_json("#/components/schemas/Health"),
                    "503": {
                        "description": "A required dependency failed",
                        "content": {"application/json": {"schema": {"$ref": "#/components/schemas/Health"}}},
                    },
                },
                auth=False,
            )
        },
        "/api/auth/login": {
            "post": _op(
                "auth",
                "Create a session token",
                _json(
                    {
                        "type": "object",
                        "required": ["token", "username", "role", "must_change_password"],
                        "properties": {
                            "token": {"type": "string", "description": "Send as Authorization: Bearer."},
                            "username": {"type": "string"},
                            "role": {"type": "string"},
                            "must_change_password": {"type": "boolean"},
                        },
                    }
                ),
                description="JSON login for a native client. Does not require X-XSRFToken. Rate limited with the HTML login.",
                body={
                    "required": True,
                    "content": {
                        "application/json": {
                            "schema": {
                                "type": "object",
                                "required": ["username", "password"],
                                "properties": {
                                    "username": {"type": "string"},
                                    "password": {"type": "string"},
                                },
                            }
                        }
                    },
                },
                auth=False,
            )
        },
        "/api/auth/logout": {
            "post": _op(
                "auth",
                "Revoke the current session",
                _json({"type": "object", "properties": {"ok": {"type": "boolean"}}}),
                description="Accepts the session bearer token or the session cookie.",
            )
        },
        "/login": {
            "get": _op("auth", "Login page", {"200": {"description": "HTML. Sets the `_xsrf` cookie."}}, auth=False),
            "post": _op(
                "auth",
                "Create a browser session",
                {"302": {"description": "Session cookies set, redirect to /files/"}},
                description="Form login for the browser. Native clients use the bearer token instead.",
                body=_form(
                    {"username": {"type": "string"}, "password": {"type": "string"}, "_xsrf": {"type": "string"}},
                    ["username", "password", "_xsrf"],
                ),
                params=[{"$ref": "#/components/parameters/xsrf"}],
                auth=False,
            ),
        },
        "/logout": {
            "post": _op("auth", "End the browser session", {"302": {"description": "Cookies cleared"}}, params=_write_params())
        },
        "/api/features": {
            "get": _op(
                "account",
                "Feature flags",
                _json({"type": "object", "additionalProperties": {"type": "boolean"}}),
                description="Boolean map. Keys include file_upload, file_download, file_share, super_search, direct_messages.",
            )
        },
        "/api/runtime-config": {
            "get": _op(
                "transfers",
                "Transfer profile",
                _json({"type": "object"}),
                description="DB-backed upload chunk size, parallelism, and profile name.",
            )
        },
        "/api/files/{path}": {
            "get": _op(
                "files",
                "List a directory",
                {**_json("#/components/schemas/FileList"), **_err()},
                params=[path_param],
            )
        },
        "/files/{path}": {
            "get": _op(
                "files",
                "View or download a file",
                {
                    "200": {
                        "description": "File bytes, or HTML when neither download nor mode=raw is set",
                        "content": {"application/octet-stream": {"schema": {"type": "string", "format": "binary"}}},
                    },
                    **_err(),
                },
                params=[
                    path_param,
                    {
                        "name": "download",
                        "in": "query",
                        "schema": {"type": "string"},
                        "description": "Any value, including `1`, returns the file as a download. Supports `Range`.",
                    },
                    {
                        "name": "mode",
                        "in": "query",
                        "schema": {"type": "string", "enum": ["view", "raw"]},
                    },
                ],
            ),
            "head": _op("files", "File metadata via headers", {"200": {"description": "Same headers as GET, no body"}}, params=[path_param]),
        },
        "/upload": {
            "post": _op(
                "transfers",
                "Upload a small file",
                {"200": {"description": "Stored"}, **_err()},
                description="Body is the raw file. Destination comes from headers, or from the query names `upload_dir` and `upload_filename`. Use the ranged session once the file reaches the large-file threshold (default 100 MB).",
                params=[
                    *_write_params(),
                    {"name": "X-Upload-Dir", "in": "header", "schema": {"type": "string"}},
                    {"name": "X-Upload-Filename", "in": "header", "required": True, "schema": {"type": "string"}},
                ],
                body={"required": True, "content": {"application/octet-stream": {"schema": {"type": "string", "format": "binary"}}}},
            )
        },
        "/api/upload/range/session": {
            "post": _op(
                "transfers",
                "Open a ranged upload",
                {
                    "201": {"description": "New session", "content": {"application/json": {"schema": {"$ref": "#/components/schemas/RangeSession"}}}},
                    "200": {"description": "Existing session resumed", "content": {"application/json": {"schema": {"$ref": "#/components/schemas/RangeSession"}}}},
                    "413": {"description": "Larger than the max file size"},
                    **_err(),
                },
                params=[{"$ref": "#/components/parameters/xsrf"}],
                body={
                    "required": True,
                    "content": {
                        "application/json": {
                            "schema": {
                                "type": "object",
                                "required": ["filename", "total_size"],
                                "properties": {
                                    "upload_dir": {"type": "string"},
                                    "filename": {"type": "string"},
                                    "total_size": {"type": "integer"},
                                },
                            }
                        }
                    },
                },
            )
        },
        "/api/upload/range/{upload_id}": {
            "put": _op(
                "transfers",
                "Write one byte range",
                {
                    "200": {"description": "Chunk stored", "content": {"application/json": {"schema": {"type": "object", "properties": {"status": {"type": "string", "enum": ["chunk_received"]}}}}}},
                    "201": {"description": "File complete", "content": {"application/json": {"schema": {"type": "object", "properties": {"status": {"type": "string", "enum": ["complete"]}, "message": {"type": "string"}}}}}},
                    **_err(),
                },
                description="Body is the chunk. `Content-Range: bytes <start>-<end>/<total>` is required. Chunks are written in place.",
                params=[
                    upload_id,
                    {"$ref": "#/components/parameters/xsrf"},
                    {"name": "Content-Range", "in": "header", "required": True, "schema": {"type": "string"}},
                ],
                body={"required": True, "content": {"application/octet-stream": {"schema": {"type": "string", "format": "binary"}}}},
            ),
            "delete": _op(
                "transfers",
                "Abort a ranged upload",
                _json({"type": "object", "properties": {"status": {"type": "string"}}}),
                params=[upload_id, {"$ref": "#/components/parameters/xsrf"}],
            ),
        },
        "/api/upload/range/{upload_id}/status": {
            "get": _op(
                "transfers",
                "Ranged upload progress",
                _json(
                    {
                        "type": "object",
                        "properties": {
                            "upload_id": {"type": "string"},
                            "total_size": {"type": "integer"},
                            "ranges": {"type": "array", "items": {"type": "array", "items": {"type": "integer"}}},
                            "complete": {"type": "boolean"},
                            "transfer_profile": {"type": "string"},
                            "chunk_bytes": {"type": "integer"},
                        },
                    }
                ),
                params=[upload_id],
            ),
            "delete": _op(
                "transfers",
                "Discard an incomplete ranged upload",
                _json({"type": "object"}),
                params=[upload_id, {"$ref": "#/components/parameters/xsrf"}],
            ),
        },
        "/mkdir": {
            "post": _op(
                "files",
                "Create a folder",
                {**_json("#/components/schemas/MutationOk"), "409": {"description": "Already exists"}, **_err()},
                params=_write_params(),
                body=_form({"parent": {"type": "string"}, "name": {"type": "string"}}, ["name"]),
            )
        },
        "/delete": {
            "post": _op(
                "files",
                "Delete a file or folder",
                {**_json("#/components/schemas/MutationOk"), **_err()},
                params=_write_params(),
                body=_form({"path": {"type": "string"}}, ["path"]),
            )
        },
        "/rename": {
            "post": _op(
                "files",
                "Rename a file or folder",
                {**_json("#/components/schemas/MutationOk"), **_err()},
                params=_write_params(),
                body=_form({"path": {"type": "string"}, "new_name": {"type": "string"}}, ["path", "new_name"]),
            )
        },
        "/copy": {
            "post": _op(
                "files",
                "Copy a file or folder",
                {**_json("#/components/schemas/MutationOk"), **_err()},
                params=_write_params(),
                body=_form({"path": {"type": "string"}, "dest": {"type": "string"}}, ["path", "dest"]),
            )
        },
        "/move": {
            "post": _op(
                "files",
                "Move a file or folder",
                {**_json("#/components/schemas/MutationOk"), **_err()},
                params=_write_params(),
                body=_form({"path": {"type": "string"}, "dest": {"type": "string"}}, ["path", "dest"]),
            )
        },
        "/api/folder-size": {
            "get": _op(
                "files",
                "Recursive folder size",
                _json(
                    {
                        "type": "object",
                        "properties": {
                            "path": {"type": "string"},
                            "bytes": {"type": "integer"},
                            "files": {"type": "integer"},
                            "size_str": {"type": "string"},
                        },
                    }
                ),
                params=[{"name": "path", "in": "query", "required": True, "schema": {"type": "string"}}],
            )
        },
        "/api/download/zip": {
            "post": _op(
                "transfers",
                "Download paths as a zip",
                {"200": {"description": "Zip bytes", "content": {"application/zip": {"schema": {"type": "string", "format": "binary"}}}}, **_err()},
                params=[{"$ref": "#/components/parameters/xsrf"}],
                body={
                    "required": True,
                    "content": {
                        "application/json": {
                            "schema": {
                                "type": "object",
                                "required": ["paths"],
                                "properties": {
                                    "paths": {"type": "array", "items": {"type": "string"}},
                                    "filename": {"type": "string"},
                                },
                            }
                        }
                    },
                },
            )
        },
        "/api/bulk/ticket": {
            "post": _op(
                "transfers",
                "Mint a short-lived bulk transfer ticket",
                _json(
                    {
                        "type": "object",
                        "properties": {
                            "ticket": {"type": "string"},
                            "port": {"type": "integer"},
                            "expires": {"type": "integer"},
                            "ttl": {"type": "integer"},
                        },
                    }
                ),
                description="PUT needs upload_dir and filename. GET needs relpath. Token-only sessions cannot mint tickets.",
                params=[{"$ref": "#/components/parameters/xsrf"}],
                body={
                    "required": True,
                    "content": {
                        "application/json": {
                            "schema": {
                                "type": "object",
                                "required": ["op", "size"],
                                "properties": {
                                    "op": {"type": "string", "enum": ["PUT", "GET"]},
                                    "size": {"type": "integer"},
                                    "upload_dir": {"type": "string"},
                                    "filename": {"type": "string"},
                                    "relpath": {"type": "string"},
                                },
                            }
                        }
                    },
                },
            )
        },
        "/share/create": {
            "post": _op(
                "shares",
                "Create a share",
                _json(
                    {
                        "type": "object",
                        "properties": {
                            "id": {"type": "string"},
                            "url": {"type": "string"},
                            "secret_token": {"type": "string"},
                        },
                    }
                ),
                params=[{"$ref": "#/components/parameters/xsrf"}],
                body={"required": True, "content": {"application/json": {"schema": {"$ref": "#/components/schemas/ShareCreate"}}}},
            )
        },
        "/share/list": {
            "get": _op(
                "shares",
                "List shares",
                _json(
                    {
                        "type": "object",
                        "properties": {
                            "shares": {"type": "object", "additionalProperties": {"type": "object"}},
                            "shared_with_me": {"type": "array", "items": {"type": "object"}},
                        },
                    }
                ),
            )
        },
        "/share/revoke": {
            "post": _op(
                "shares",
                "Revoke a share",
                _json({"type": "object"}),
                params=_write_params(),
                body=_form({"id": {"type": "string"}}, ["id"]),
            )
        },
        "/api/share/details": {
            "get": _op(
                "shares",
                "Shares that include a path",
                _json({"type": "object", "properties": {"shares": {"type": "array", "items": {"type": "object"}}}}),
                params=[{"name": "path", "in": "query", "required": True, "schema": {"type": "string"}}],
            )
        },
        "/api/share/details_by_id": {
            "get": _op(
                "shares",
                "One share by id",
                _json({"type": "object"}),
                params=[{"name": "id", "in": "query", "required": True, "schema": {"type": "string"}}],
            )
        },
        "/api/favorites": {
            "get": _op("account", "List favorites", _json({"type": "object", "properties": {"favorites": {"type": "array"}}}))
        },
        "/api/favorites/toggle": {
            "post": _op(
                "account",
                "Toggle a favorite",
                _json({"type": "object", "properties": {"favorited": {"type": "boolean"}, "path": {"type": "string"}}}),
                params=[{"$ref": "#/components/parameters/xsrf"}],
                body={
                    "required": True,
                    "content": {"application/json": {"schema": {"type": "object", "required": ["path"], "properties": {"path": {"type": "string"}}}}},
                },
            )
        },
        "/api/tags": {
            "get": _op("account", "Tag catalog", _json({"type": "object"}))
        },
        "/api/users/search": {
            "get": _op(
                "shares",
                "Search usernames",
                _json({"type": "object", "properties": {"users": {"type": "array", "items": {"type": "object", "properties": {"username": {"type": "string"}}}}}}),
                params=[{"name": "q", "in": "query", "required": True, "schema": {"type": "string", "minLength": 2}}],
            )
        },
        "/api/sessions": {
            "get": _op("auth", "List sessions for the current user", _json({"type": "object"}))
        },
        "/api/sessions/{session_id}/revoke": {
            "post": _op(
                "auth",
                "Revoke a session",
                _json({"type": "object"}),
                params=[
                    {"name": "session_id", "in": "path", "required": True, "schema": {"type": "string"}},
                    {"$ref": "#/components/parameters/xsrf"},
                ],
            )
        },
        "/api/cloud/providers": {
            "get": _op("files", "Configured cloud providers", _json({"type": "object"}))
        },
        **_chat_paths(),
        "/search/ws": {"get": _ws("Super Search. JSON text frames. First message is the query.")},
        "/stream/{path}": {"get": _ws("Tail a log file.", params=[path_param])},
        "/features": {"get": _ws("Feature-flag push. Payload matches GET /api/features.")},
        "/runtime-config": {"get": _ws("Transfer-profile push. Payload matches GET /api/runtime-config.")},
        "/ws/file-transfer": {"get": _ws("Legacy upload socket. The browser uses HTTP ranges instead.")},
        "/ws/bulk": {"get": _ws("Bulk upload. Binary frames after POST /api/bulk/ticket.")},
        "/ws/policy-decisions": {"get": _ws("ABAC decision stream for an admin session.")},
    }


def _chat_paths() -> dict[str, Any]:
    """Direct-message routes. GitLab and OneDrive are intentionally omitted."""
    xsrf = {"$ref": "#/components/parameters/xsrf"}
    cid = {"name": "conversation_id", "in": "path", "required": True, "schema": {"type": "string"}}
    mid = {"name": "message_id", "in": "path", "required": True, "schema": {"type": "string"}}
    session = [{"bearerAuth": []}, {"sessionCookie": []}]
    note = (
        "User session from POST /api/auth/login or the session cookie. "
        "Needs the chat extra and the direct_messages flag. "
        "The server access token is rejected."
    )
    obj = {"type": "object"}
    ok = _json(obj)

    def chat(summary: str, responses: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
        kwargs.setdefault("description", note)
        return _op("chat", summary, responses, security=session, **kwargs)

    def json_body(properties: dict[str, Any], required: list[str] | None = None) -> dict[str, Any]:
        schema: dict[str, Any] = {"type": "object", "properties": properties}
        if required:
            schema["required"] = required
        return {"required": True, "content": {"application/json": {"schema": schema}}}

    return {
        "/chat": {"get": chat("Chat page", {"200": {"description": "HTML"}})},
        "/api/chat/conversations": {
            "get": chat("List conversations", _json({"type": "object", "properties": {"conversations": {"type": "array", "items": obj}}})),
            "post": chat(
                "Open a DM or create a group",
                _json({"type": "object", "properties": {"conversation": obj}}),
                params=[xsrf],
                body=json_body(
                    {
                        "username": {"type": "string", "description": "DM recipient. Omit when creating a group."},
                        "members": {"type": "array", "items": {"type": "string"}, "description": "Group members. Presence of this field creates a group."},
                        "title": {"type": "string"},
                    }
                ),
            ),
        },
        "/api/chat/conversations/{conversation_id}/messages": {
            "get": chat(
                "List messages",
                _json({"type": "object", "properties": {"messages": {"type": "array"}, "pins": {"type": "array"}, "receipts": {"type": "array"}}}),
                params=[
                    cid,
                    {"name": "before_id", "in": "query", "schema": {"type": "string"}},
                    {"name": "after_id", "in": "query", "schema": {"type": "string"}},
                    {"name": "limit", "in": "query", "schema": {"type": "integer", "default": 50}},
                ],
            ),
            "post": chat(
                "Send a text message",
                _json({"type": "object", "properties": {"message": obj}}),
                description=note + " Non-text types must use /attach.",
                params=[cid, xsrf],
                body=json_body(
                    {
                        "body": {"type": "string"},
                        "type": {"type": "string", "enum": ["text"]},
                        "reply_to_id": {"type": "string"},
                        "e2e": {"type": "object", "description": "Client ciphertext. When set, body is stored empty."},
                    }
                ),
            ),
        },
        "/api/chat/conversations/{conversation_id}/messages/{message_id}": {
            "patch": chat(
                "Edit a message",
                _json({"type": "object", "properties": {"message": obj}}),
                params=[cid, mid, xsrf],
                body=json_body({"body": {"type": "string"}, "e2e": {"type": "object"}}),
            ),
            "delete": chat(
                "Delete a message",
                _json({"type": "object", "properties": {"deleted": {"type": "boolean"}, "message_id": {"type": "string"}}}),
                params=[cid, mid, xsrf],
            ),
        },
        "/api/chat/conversations/{conversation_id}/messages/{message_id}/file": {
            "get": chat(
                "Download a message attachment",
                {"200": {"description": "File bytes, an image preview, or a folder zip"}, "302": {"description": "Folder redirected to a share URL"}, **_err()},
                params=[cid, mid, {"name": "i", "in": "query", "schema": {"type": "integer", "default": 0}}],
            )
        },
        "/api/chat/conversations/{conversation_id}/messages/{message_id}/save-copy": {
            "post": chat("Save an attachment into the user's files", ok, params=[cid, mid, xsrf, {"name": "i", "in": "query", "schema": {"type": "integer", "default": 0}}])
        },
        "/api/chat/conversations/{conversation_id}/messages/{message_id}/reactions": {
            "post": chat(
                "Toggle a reaction",
                _json({"type": "object", "properties": {"reactions": {"type": "array"}}}),
                params=[cid, mid, xsrf],
                body=json_body({"emoji": {"type": "string"}}, ["emoji"]),
            )
        },
        "/api/chat/conversations/{conversation_id}/messages/{message_id}/pin": {
            "post": chat("Pin a message", _json({"type": "object", "properties": {"pins": {"type": "array"}}}), params=[cid, mid, xsrf]),
            "delete": chat("Unpin a message", _json({"type": "object", "properties": {"pins": {"type": "array"}}}), params=[cid, mid, xsrf]),
        },
        "/api/chat/conversations/{conversation_id}/messages/{message_id}/forward": {
            "post": chat(
                "Forward a message",
                _json({"type": "object", "properties": {"messages": {"type": "array"}}}),
                description=note + " Encrypted messages are rejected; the client re-encrypts them.",
                params=[cid, mid, xsrf],
                body=json_body({"conversation_ids": {"type": "array", "items": {"type": "string"}}}, ["conversation_ids"]),
            )
        },
        "/api/chat/conversations/{conversation_id}/attach": {
            "post": chat(
                "Attach files",
                _json({"type": "object", "properties": {"message": obj}}),
                params=[cid, xsrf],
                body={
                    "required": True,
                    "content": {
                        "multipart/form-data": {
                            "schema": {
                                "type": "object",
                                "properties": {
                                    "file": {"type": "array", "items": {"type": "string", "format": "binary"}},
                                    "source_path": {"type": "array", "items": {"type": "string"}, "description": "Paths already in the sender's files."},
                                    "caption": {"type": "string"},
                                    "body": {"type": "string"},
                                    "reply_to_id": {"type": "string"},
                                    "e2e": {"type": "string", "description": "JSON string of the ciphertext metadata."},
                                },
                            }
                        }
                    },
                },
            )
        },
        "/api/chat/conversations/{conversation_id}/members": {
            "post": chat(
                "Add a member",
                _json({"type": "object", "properties": {"member": obj, "conversation": obj}}),
                params=[cid, xsrf],
                body=json_body({"username": {"type": "string"}}, ["username"]),
            ),
            "delete": chat(
                "Remove a member",
                _json({"type": "object", "properties": {"removed": {"type": "string"}}}),
                description=note + " Omit username to leave the conversation yourself.",
                params=[cid, xsrf, {"name": "username", "in": "query", "schema": {"type": "string"}}],
            ),
        },
        "/api/chat/conversations/{conversation_id}/mute": {
            "post": chat(
                "Mute or unmute",
                _json({"type": "object", "properties": {"muted": {"type": "boolean"}}}),
                params=[cid, xsrf],
                body=json_body({"muted": {"type": "boolean"}}, ["muted"]),
            )
        },
        "/api/chat/conversations/{conversation_id}/request": {
            "post": chat(
                "Accept or decline a chat request",
                _json({"type": "object", "properties": {"ok": {"type": "boolean"}, "conversation": obj}}),
                params=[cid, xsrf],
                body=json_body({"action": {"type": "string", "enum": ["accept", "decline"]}}, ["action"]),
            )
        },
        "/api/chat/conversations/{conversation_id}/e2e": {
            "get": chat("Conversation key wraps and member identity keys", _json({"type": "object", "properties": {"wraps": obj, "keys": obj}}), params=[cid]),
            "put": chat(
                "Store wrapped conversation keys",
                _json({"type": "object", "properties": {"wraps": obj}}),
                params=[cid, xsrf],
                body=json_body({"wraps": {"type": "object", "additionalProperties": {"type": "string"}}}, ["wraps"]),
            ),
        },
        "/api/chat/e2e/keys": {
            "get": chat(
                "Public identity keys",
                _json({"type": "object", "properties": {"keys": obj}}),
                params=[{"name": "users", "in": "query", "schema": {"type": "string"}, "description": "Comma-separated usernames. Defaults to the caller."}],
            ),
            "put": chat(
                "Publish this user's identity key",
                _json({"type": "object", "properties": {"ok": {"type": "boolean"}, "public_jwk": obj}}),
                params=[xsrf],
                body=json_body({"public_jwk": obj}, ["public_jwk"]),
            ),
        },
        "/api/chat/e2e/identity": {
            "get": chat("Load the encrypted identity backup", _json({"type": "object", "properties": {"backup": obj}})),
            "put": chat(
                "Save the encrypted identity backup",
                _json({"type": "object", "properties": {"ok": {"type": "boolean"}, "backup": obj, "public_jwk": obj}}),
                params=[xsrf],
                body=json_body({"backup": obj}),
            ),
        },
        "/api/chat/search": {
            "get": chat(
                "Search messages",
                _json({"type": "object", "properties": {"results": {"type": "array"}}}),
                params=[{"name": "q", "in": "query", "schema": {"type": "string"}}],
            )
        },
        "/api/chat/unread": {
            "get": chat(
                "Unread counts",
                _json({"type": "object", "properties": {"total": {"type": "integer"}, "conversations": {"type": "array", "items": {"type": "object", "properties": {"id": {"type": "string"}, "count": {"type": "integer"}}}}}}),
            )
        },
        "/api/chat/shared-with-me": {
            "get": chat(
                "Files received in chat",
                _json({"type": "object", "properties": {"senders": {"type": "array"}, "total_files": {"type": "integer"}, "items": {"type": "array"}}}),
            )
        },
        "/api/chat/shared-with-me/{share_id}": {
            "delete": chat(
                "Hide a received chat file",
                _json({"type": "object", "properties": {"deleted": {"type": "boolean"}, "id": {"type": "string"}}}),
                params=[{"name": "share_id", "in": "path", "required": True, "schema": {"type": "string"}}, xsrf],
            )
        },
        "/ws/chat": {
            "get": _op(
                "chat",
                "Chat socket",
                {"101": {"description": "WebSocket upgrade. First server frame is {\"type\":\"chat_ready\"}."}},
                description=(
                    note
                    + " Client frames: chat_send (conversation_id, body or e2e, reply_to_id),"
                    + " chat_read (conversation_id, message_id, decrypted),"
                    + " chat_typing, chat_e2e_need_key,"
                    + " enhanced_invite, enhanced_join, enhanced_msg, enhanced_receipt, enhanced_close."
                ),
                security=session,
            )
        },
    }


def _ws(summary: str, *, params: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    operation = _op(
        "realtime",
        summary,
        {"101": {"description": "WebSocket upgrade"}},
        description="Open with the same cookies or bearer token as the HTTP API. The browser also checks `Origin`.",
    )
    if params:
        operation["parameters"] = params
    return operation
