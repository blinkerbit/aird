"""Additional Starlette routes not yet covered by other routers."""

from __future__ import annotations

import asyncio
import base64
import concurrent.futures
import json
import logging
import mmap
import os
import pathlib
import secrets
import tempfile
from typing import Any

import aiofiles
from starlette.requests import Request
from starlette.responses import JSONResponse, PlainTextResponse, RedirectResponse, Response, StreamingResponse
from starlette.routing import Route
from webauthn import (
    generate_authentication_options,
    generate_registration_options,
    options_to_json,
    verify_authentication_response,
)
from webauthn.helpers import parse_authentication_credential_json
from webauthn.helpers.cose import COSEAlgorithmIdentifier
from webauthn.helpers.structs import (
    AuthenticatorSelectionCriteria,
    PublicKeyCredentialDescriptor,
    ResidentKeyRequirement,
    UserVerificationRequirement,
)

import aird.constants as constants_module
from aird.cloud import CloudProviderError
from aird.config import CLOUD_MANAGER, MAX_READABLE_FILE_SIZE
from aird.constants.file_ops import (
    ACCESS_DENIED_WITH_PERIOD,
    FILE_EDIT_DISABLED,
    FILE_NOT_FOUND,
    FILE_SAVED_SUCCESSFULLY,
    FILE_SAVE_ERROR,
    INVALID_JSON_REQUEST,
    INVALID_PATH,
    PROVIDER_NOT_CONFIGURED,
)
from aird.constants.input_limits import EDIT_JSON_BODY_MAX_BYTES, REL_PATH_MAX_LEN, SHARE_JSON_BODY_MAX_BYTES
from aird.core.bulk.ticket import BULK_TICKET_TTL_SEC, BulkTicketClaims, default_expiry, mint_bulk_ticket
from aird.core.bulk.tcp_server import get_bulk_tcp_port
from aird.core.file_operations import get_files_by_tag_patterns
from aird.core.input_validation import validate_share_update_struct
from aird.core.mmap_handler import MMapFileHandler
from aird.core.security import is_within_root
from aird.core.webauthn_config import resolve_webauthn_config
from aird.db import webauthn as webauthn_db
from aird.db.resource_tags import list_resource_tags
from aird.db.tag_colors import get_tag_colors_map
from aird.handlers.auth_handlers import _apply_session_cookies, check_login_rate_limit
from aird.handlers.base_handler import (
    get_username_string_for_db,
    login_matches_share_creator_field,
)
from aird.handlers.bulk_handlers import _TOKEN_ONLY_USERNAMES
from aird.handlers.constants import (
    APPLICATION_OCTET_STREAM,
    AUTH_REQUIRED,
    DB_UNAVAILABLE_MSG,
    FILES_BASE_URL,
    FS_DISABLED_MSG,
    INVALID_JSON_MSG,
)
from aird.handlers.file_op_handlers import HEADER_APPLICATION_JSON, path_to_rel
from aird.handlers.share_handlers import (
    _execute_share_update,
    _share_update_payload_for_user,
    _validate_share_update_request,
    cleanup_share_cloud_dir_if_empty,
    remove_cloud_file_if_exists,
)
from aird.handlers.webauthn_handlers import (
    PURPOSE_AUTH,
    PURPOSE_REGISTER,
    _decode_challenge_from_credential,
    _pad_b64,
    _parse_registration_credential,
    _verify_registration,
)
from aird.ms.ctx import check_xsrf_token, current_user, get_state, user_root_for
from aird.ms.deps import db_conn, get_service, is_admin_user, username_from_user
from aird.ms.render import render_html
from aird.ms.routers._helpers import (
    has_modify_privileges,
    request_mounts,
    require_feature,
    require_user,
    resolve_request_rel,
)
from aird.ms.routers.admin import require_admin
from aird.ms.routers.auth import _HandlerBridge, _settings
from aird.utils.tag_display import tag_chip_inline_style
from aird.utils.util import get_current_feature_flags, get_file_icon, is_feature_enabled, sanitize_cloud_filename

logger = logging.getLogger(__name__)

_TOKEN_USERNAMES = frozenset(
    {"token_user", "admin_token", "token_authenticated", "admin_token_authenticated"}
)


def _client_ip(request: Request) -> str:
    if request.client:
        return request.client.host
    return ""


def _json_error(status: int, error: str) -> JSONResponse:
    return JSONResponse({"error": error}, status_code=status)


def _db_or_503(request: Request):
    conn = db_conn(request)
    if not conn:
        return None, _json_error(503, DB_UNAVAILABLE_MSG)
    return conn, None


def _webauthn_enabled() -> bool:
    return is_feature_enabled("webauthn", False)


class _WebAuthnRequestAdapter:
    def __init__(self, request: Request):
        self.request = request


def _cloud_manager(request: Request):
    state = get_state(request)
    ctx = state.settings.get("app_context")
    if ctx is not None and getattr(ctx, "cloud_manager", None) is not None:
        return ctx.cloud_manager
    return state.settings.get("cloud_manager", CLOUD_MANAGER)


def _handle_cloud_error(exc: Exception, log_msg: str, client_msg: str) -> JSONResponse:
    if isinstance(exc, CloudProviderError):
        return JSONResponse({"error": str(exc)}, status_code=400)
    logger.exception(log_msg)
    return JSONResponse({"error": client_msg}, status_code=500)


# ---------------------------------------------------------------------------
# WebAuthn
# ---------------------------------------------------------------------------


async def webauthn_status(request: Request) -> Response:
    if not _webauthn_enabled():
        return Response(status_code=404)
    adapter = _WebAuthnRequestAdapter(request)
    rp_id, _, _ = resolve_webauthn_config(adapter)
    return JSONResponse({"enabled": True, "rpId": rp_id})


async def webauthn_register_options(request: Request) -> Response:
    if not _webauthn_enabled():
        return Response(status_code=404)
    user = current_user(request)
    if not user:
        return _json_error(401, AUTH_REQUIRED)
    if not isinstance(user, dict) or not user.get("username"):
        return _json_error(403, "Passkeys require a full user account.")
    username = user["username"]
    if username in _TOKEN_USERNAMES:
        return _json_error(403, "Passkeys are not available for token sessions.")

    conn, err = _db_or_503(request)
    if err:
        return err

    adapter = _WebAuthnRequestAdapter(request)
    rp_id, rp_name, _ = resolve_webauthn_config(adapter)
    existing = webauthn_db.list_credentials(conn, username)
    exclude = [
        PublicKeyCredentialDescriptor(id=base64.urlsafe_b64decode(_pad_b64(c["credential_id"])))
        for c in existing
    ]
    options = generate_registration_options(
        rp_id=rp_id,
        rp_name=rp_name,
        user_name=username,
        user_id=username.encode("utf-8"),
        user_display_name=username,
        authenticator_selection=AuthenticatorSelectionCriteria(
            resident_key=ResidentKeyRequirement.PREFERRED,
            user_verification=UserVerificationRequirement.PREFERRED,
        ),
        exclude_credentials=exclude or None,
        supported_pub_key_algs=[
            COSEAlgorithmIdentifier.ECDSA_SHA_256,
            COSEAlgorithmIdentifier.RSASSA_PKCS1_v1_5_SHA_256,
        ],
    )
    if not webauthn_db.store_challenge(conn, options.challenge, PURPOSE_REGISTER, username):
        return _json_error(500, "Could not store challenge.")

    prf_salt = webauthn_db.ensure_prf_salt(conn, username)
    payload = json.loads(options_to_json(options))
    payload["extensions"] = {"prf": {}}
    if prf_salt:
        payload["prfSalt"] = prf_salt
    return JSONResponse(payload)


async def webauthn_register_verify(request: Request) -> Response:
    if not _webauthn_enabled():
        return Response(status_code=404)
    user = current_user(request)
    if not user or not isinstance(user, dict) or not user.get("username"):
        return _json_error(403, "Forbidden")
    username = user["username"]

    conn, err = _db_or_503(request)
    if err:
        return err

    try:
        body = json.loads((await request.body()) or b"{}")
    except json.JSONDecodeError:
        return _json_error(400, INVALID_JSON_MSG)

    credential = _parse_registration_credential(body)
    if credential is None:
        return _json_error(400, "Invalid credential.")

    challenge_bytes = _decode_challenge_from_credential(credential)
    if challenge_bytes is None:
        return _json_error(400, "Invalid client data.")

    adapter = _WebAuthnRequestAdapter(request)
    rp_id, _, origins = resolve_webauthn_config(adapter)
    cred_id_b64, verify_err = _verify_registration(
        conn, username, body, credential, challenge_bytes, rp_id, origins
    )
    if verify_err:
        return _json_error(400, verify_err)
    return JSONResponse({"ok": True, "id": cred_id_b64})


async def webauthn_auth_options(request: Request) -> Response:
    if not _webauthn_enabled():
        return Response(status_code=404)

    conn, err = _db_or_503(request)
    if err:
        return err

    try:
        body = json.loads((await request.body()) or b"{}")
    except json.JSONDecodeError:
        return _json_error(400, INVALID_JSON_MSG)

    username = (body.get("username") or "").strip()
    if not username:
        return _json_error(400, "Username is required.")

    creds = webauthn_db.list_credentials(conn, username)
    if not creds:
        return _json_error(400, "Passkey sign-in is not available for this account.")

    adapter = _WebAuthnRequestAdapter(request)
    rp_id, _, _ = resolve_webauthn_config(adapter)
    allow = [
        PublicKeyCredentialDescriptor(
            id=base64.urlsafe_b64decode(_pad_b64(c["credential_id"]))
        )
        for c in creds
    ]
    options = generate_authentication_options(
        rp_id=rp_id,
        allow_credentials=allow,
        user_verification=UserVerificationRequirement.PREFERRED,
    )
    if not webauthn_db.store_challenge(conn, options.challenge, PURPOSE_AUTH, username):
        return _json_error(500, "Could not store challenge.")
    return JSONResponse(json.loads(options_to_json(options)))


async def webauthn_auth_verify(request: Request) -> Response:
    if not _webauthn_enabled():
        return Response(status_code=404)

    if not check_login_rate_limit(_client_ip(request)):
        return _json_error(429, "Too many login attempts.")

    conn, err = _db_or_503(request)
    if err:
        return err

    try:
        body = json.loads((await request.body()) or b"{}")
    except json.JSONDecodeError:
        return _json_error(400, INVALID_JSON_MSG)

    try:
        credential = parse_authentication_credential_json(json.dumps(body.get("credential", body)))
    except Exception:
        return _json_error(400, "Invalid credential.")

    cred_id_b64 = webauthn_db.credential_id_to_b64(credential.raw_id)
    stored = webauthn_db.get_credential_by_credential_id(conn, cred_id_b64)
    if not stored:
        return _json_error(400, "Unknown passkey.")

    challenge_bytes = _decode_challenge_from_credential(credential)
    if challenge_bytes is None:
        return _json_error(400, "Invalid client data.")

    challenge_user = webauthn_db.consume_challenge(conn, challenge_bytes, PURPOSE_AUTH)
    if challenge_user is None or challenge_user != stored["username"]:
        return _json_error(400, "Challenge expired or invalid.")

    adapter = _WebAuthnRequestAdapter(request)
    rp_id, _, origins = resolve_webauthn_config(adapter)
    try:
        verified = verify_authentication_response(
            credential=credential,
            expected_challenge=challenge_bytes,
            expected_rp_id=rp_id,
            expected_origin=origins,
            credential_public_key=stored["public_key"],
            credential_current_sign_count=stored["sign_count"],
            require_user_verification=False,
        )
    except Exception as exc:
        logger.info("webauthn auth verify failed: %s", exc)
        return _json_error(400, "Passkey authentication failed.")

    webauthn_db.update_sign_count(conn, stored["id"], verified.new_sign_count)

    user_service = get_service(request, "user_service")
    user = user_service.get_user(conn, stored["username"]) if user_service else None
    if not user:
        return _json_error(403, "User account not found.")

    user_role = user.get("role", "user")
    bridge = _HandlerBridge(request, _settings(request))
    _apply_session_cookies(bridge, stored["username"], user_role)

    next_url = (body.get("next") or FILES_BASE_URL).strip()
    if not next_url.startswith("/") or next_url.startswith("//"):
        next_url = FILES_BASE_URL

    response = JSONResponse({"ok": True, "redirect": next_url})
    bridge.apply_cookies(response)
    return response


async def webauthn_credential_delete(request: Request) -> Response:
    if not _webauthn_enabled():
        return Response(status_code=404)
    user = current_user(request)
    if not user or not isinstance(user, dict) or not user.get("username"):
        return _json_error(403, "Forbidden")
    username = user["username"]

    conn, err = _db_or_503(request)
    if err:
        return err

    cred_id = request.path_params.get("cred_id", "")
    try:
        cred_db_id = int(cred_id)
    except (TypeError, ValueError):
        return _json_error(400, "Invalid credential id.")

    if webauthn_db.delete_credential(conn, cred_db_id, username):
        return JSONResponse({"ok": True})
    return _json_error(404, "Passkey not found.")


# ---------------------------------------------------------------------------
# Cloud API
# ---------------------------------------------------------------------------


async def cloud_providers(request: Request) -> Response:
    user = require_user(request)
    if not isinstance(user, dict):
        return RedirectResponse("/login", status_code=302)

    manager = _cloud_manager(request)
    providers = [
        {
            "name": provider.name,
            "label": provider.label,
            "root": provider.root_identifier,
        }
        for provider in manager.list_providers()
    ]
    return JSONResponse({"providers": providers})


async def cloud_files(request: Request) -> Response:
    user = require_user(request)
    if not isinstance(user, dict):
        return RedirectResponse("/login", status_code=302)

    provider_name = request.path_params["provider_name"]
    manager = _cloud_manager(request)
    provider = manager.get(provider_name)
    if not provider:
        return JSONResponse({"error": PROVIDER_NOT_CONFIGURED}, status_code=404)

    folder_id = request.query_params.get("folder", provider.root_identifier)
    try:
        files = await asyncio.to_thread(
            provider.list_files, folder_id or provider.root_identifier
        )
    except Exception as exc:
        return _handle_cloud_error(
            exc,
            f"Failed to list cloud files for provider {provider_name}",
            "Failed to load cloud files",
        )

    return JSONResponse(
        {
            "provider": provider_name,
            "folder": folder_id or provider.root_identifier,
            "files": [cloud_file.to_dict() for cloud_file in files],
        }
    )


async def cloud_download(request: Request) -> Response:
    user = require_user(request)
    if not isinstance(user, dict):
        return RedirectResponse("/login", status_code=302)

    provider_name = request.path_params["provider_name"]
    manager = _cloud_manager(request)
    provider = manager.get(provider_name)
    if not provider:
        return JSONResponse({"error": PROVIDER_NOT_CONFIGURED}, status_code=404)

    file_id = (request.query_params.get("file_id") or "").strip()
    if not file_id:
        return JSONResponse({"error": "file_id is required"}, status_code=400)

    requested_name = (request.query_params.get("file_name") or "").strip()
    try:
        download = await asyncio.to_thread(provider.download_file, file_id)
    except Exception as exc:
        return _handle_cloud_error(
            exc,
            f"Failed to download cloud file from {provider_name}",
            "Failed to download cloud file",
        )

    filename = sanitize_cloud_filename(requested_name or getattr(download, "name", None))
    if not filename:
        filename = f"{provider_name}-file"

    disposition_name = filename.replace('"', "_")
    headers = {
        "Content-Disposition": f'attachment; filename="{disposition_name}"',
    }
    if download.content_length:
        headers["Content-Length"] = str(download.content_length)
    media_type = download.content_type or APPLICATION_OCTET_STREAM

    async def stream_body():
        iterator = download.iter_chunks()
        try:
            while True:
                chunk = await asyncio.to_thread(next, iterator, None)
                if not chunk:
                    break
                yield chunk
        finally:
            download.close()

    return StreamingResponse(stream_body(), media_type=media_type, headers=headers)


# ---------------------------------------------------------------------------
# Edit
# ---------------------------------------------------------------------------


async def edit_view(request: Request) -> Response:
    user = require_user(request)
    if not isinstance(user, dict):
        return RedirectResponse("/login", status_code=302)

    disabled = require_feature(
        "file_edit",
        True,
        "Feature disabled: File editing is currently disabled by administrator",
    )
    if disabled:
        return disabled

    if not has_modify_privileges(user):
        return PlainTextResponse("Write access denied.", status_code=403)

    path = request.path_params.get("path", "").strip().strip("/")
    if not path:
        return PlainTextResponse("Path is required", status_code=400)

    from aird.core.browse_paths import write_blocked_reason

    if write_blocked_reason(path, request_mounts(request, user)):
        return PlainTextResponse(
            "Access denied: You don't have permission to perform this action",
            status_code=403,
        )
    abspath, confine = resolve_request_rel(request, user, path)
    if not abspath or not confine or not is_within_root(abspath, confine):
        return PlainTextResponse(
            "Access denied: You don't have permission to perform this action",
            status_code=403,
        )
    if not os.path.isfile(abspath):
        return PlainTextResponse(
            "File not found: The requested file may have been moved or deleted",
            status_code=404,
        )

    try:
        file_size = os.path.getsize(abspath)
    except OSError:
        file_size = 0
    if file_size > MAX_READABLE_FILE_SIZE:
        return PlainTextResponse(
            f"File too large to edit in browser. Size: {file_size} bytes "
            f"(limit {MAX_READABLE_FILE_SIZE} bytes)",
            status_code=413,
        )

    filename = os.path.basename(abspath)
    try:
        if MMapFileHandler.should_use_mmap(file_size):
            def read_mmap():
                with open(abspath, "rb") as f:
                    with mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ) as mm:
                        return mm[:].decode("utf-8", errors="replace")

            with concurrent.futures.ThreadPoolExecutor() as executor:
                full_file_content = await asyncio.get_event_loop().run_in_executor(
                    executor, read_mmap
                )
        else:
            async with aiofiles.open(abspath, "r", encoding="utf-8", errors="replace") as f:
                full_file_content = await f.read()
    except (OSError, UnicodeDecodeError):
        async with aiofiles.open(abspath, "r", encoding="utf-8", errors="replace") as f:
            full_file_content = await f.read()

    total_lines = full_file_content.count("\n") + 1 if full_file_content else 0
    return render_html(
        request,
        "edit.html",
        filename=filename,
        path=path,
        full_file_content=full_file_content,
        total_lines=total_lines,
        features=get_current_feature_flags(),
    )


def _atomic_write(abspath: str, content: str) -> None:
    directory_name = os.path.dirname(abspath)
    os.makedirs(directory_name, exist_ok=True)
    temp_fd = tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=directory_name, delete=False)
    temp_path = temp_fd.name
    try:
        temp_fd.write(content)
        temp_fd.close()
        os.replace(temp_path, abspath)
    except Exception:
        try:
            os.unlink(temp_path)
        except OSError:
            pass
        raise


async def edit_save(request: Request) -> Response:
    user = require_user(request)
    if not isinstance(user, dict):
        return RedirectResponse("/login", status_code=302)

    if not has_modify_privileges(user):
        return PlainTextResponse("Write access denied.", status_code=403)

    if not is_feature_enabled("file_edit", True):
        return PlainTextResponse(FILE_EDIT_DISABLED, status_code=403)

    content_type = request.headers.get("Content-Type", "")
    raw_body = await request.body()
    if len(raw_body) > EDIT_JSON_BODY_MAX_BYTES:
        msg = INVALID_JSON_REQUEST if content_type.startswith(HEADER_APPLICATION_JSON) else "Bad request"
        return PlainTextResponse(msg, status_code=413)

    form_token = None
    if content_type.startswith(HEADER_APPLICATION_JSON):
        try:
            data = json.loads(raw_body.decode("utf-8", errors="replace") or "{}")
            path = data.get("path", "")
            content = data.get("content", "")
            form_token = data.get("_xsrf")
        except Exception:
            return PlainTextResponse(INVALID_JSON_REQUEST, status_code=400)
    else:
        form = await request.form()
        path = str(form.get("path", ""))
        content = str(form.get("content", ""))
        form_token = form.get("_xsrf")

    try:
        check_xsrf_token(request, str(form_token) if form_token is not None else None)
    except PermissionError:
        return PlainTextResponse("XSRF validation failed", status_code=403)

    if len(str(path).strip()) > REL_PATH_MAX_LEN:
        return PlainTextResponse(INVALID_PATH, status_code=400)

    safe_path = str(path).lstrip("/\\")
    from aird.core.browse_paths import write_blocked_reason

    if write_blocked_reason(safe_path, request_mounts(request, user)):
        return PlainTextResponse(ACCESS_DENIED_WITH_PERIOD, status_code=403)
    abspath, confine = resolve_request_rel(request, user, safe_path)
    if not abspath or not confine or not is_within_root(abspath, confine):
        return PlainTextResponse(ACCESS_DENIED_WITH_PERIOD, status_code=403)
    if not os.path.isfile(abspath):
        return PlainTextResponse(FILE_NOT_FOUND, status_code=404)

    try:
        _atomic_write(str(abspath), content)
        audit = get_service(request, "audit_service")
        if audit:
            audit.log(
                db_conn(request),
                "file_edit",
                username=username_from_user(user),
                details=path_to_rel(str(abspath), confine),
                ip=_client_ip(request),
            )
        if request.headers.get("Accept") == HEADER_APPLICATION_JSON:
            return JSONResponse({"ok": True})
        return PlainTextResponse(FILE_SAVED_SUCCESSFULLY)
    except Exception:
        logger.exception("File save error")
        return PlainTextResponse(FILE_SAVE_ERROR, status_code=500)


# ---------------------------------------------------------------------------
# Tagged files
# ---------------------------------------------------------------------------


async def tagged_files(request: Request) -> Response:
    user = require_user(request)
    if not isinstance(user, dict):
        return RedirectResponse("/login", status_code=302)

    tag_name = request.path_params["tag_name"]
    conn = db_conn(request)
    rules = list_resource_tags(conn) if conn else []
    patterns = [r["glob_pattern"] for r in rules if r.get("tag") == tag_name]
    all_tags = sorted({r["tag"] for r in rules if r.get("tag")})

    entries: list[dict[str, Any]] = []
    file_count = 0
    folder_count = 0
    if patterns:
        root = user_root_for(user)
        for path in get_files_by_tag_patterns(patterns, root):
            is_dir = path.endswith("/")
            if is_dir:
                folder_count += 1
                name = path.rstrip("/")
            else:
                file_count += 1
                name = path
            entries.append({"path": path, "name": name, "is_dir": is_dir})

    tag_colors = get_tag_colors_map(conn) if conn else {}
    return render_html(
        request,
        "tagged_files.html",
        tag_name=tag_name,
        patterns=patterns,
        entries=entries,
        file_count=file_count,
        folder_count=folder_count,
        all_tags=all_tags,
        tag_colors=tag_colors,
        tag_chip_style=tag_chip_inline_style,
        user=user,
        get_file_icon=get_file_icon,
    )


# ---------------------------------------------------------------------------
# Bulk ticket
# ---------------------------------------------------------------------------


class _BulkTicketAdapter:
    def __init__(self, request: Request, user: dict[str, Any]):
        self.request = request
        self._user = user
        self.settings = _settings(request)

    def get_current_user(self):
        return self._user


async def bulk_ticket_get(request: Request) -> Response:
    return JSONResponse(
        {
            "error": "Method not allowed",
            "hint": "POST JSON with op (PUT|GET), size, and path fields to mint a ticket.",
        },
        status_code=405,
    )


async def bulk_ticket_post(request: Request) -> Response:
    user = require_user(request)
    if not isinstance(user, dict):
        return RedirectResponse("/login", status_code=302)

    if not is_feature_enabled("file_upload", True):
        return JSONResponse({"error": "Upload disabled"}, status_code=403)

    try:
        data = json.loads((await request.body()).decode("utf-8", errors="replace") or "{}")
    except json.JSONDecodeError:
        return JSONResponse({"error": "Invalid JSON"}, status_code=400)

    op = str(data.get("op", "")).upper()
    if op not in ("PUT", "GET"):
        return JSONResponse({"error": "op must be PUT or GET"}, status_code=400)

    try:
        size = int(data.get("size", 0))
    except (TypeError, ValueError):
        return JSONResponse({"error": "Invalid size"}, status_code=400)
    if size < 0 or size > constants_module.MAX_FILE_SIZE:
        return JSONResponse({"error": "Invalid size"}, status_code=400)

    adapter = _BulkTicketAdapter(request, user)
    username = get_username_string_for_db(adapter)
    if not username or username in _TOKEN_ONLY_USERNAMES:
        return JSONResponse({"error": AUTH_REQUIRED}, status_code=403)

    secret = adapter.settings.get("cookie_secret") or ""
    if not secret:
        return JSONResponse({"error": "Server misconfigured"}, status_code=500)

    if op == "PUT":
        if not has_modify_privileges(user):
            return JSONResponse({"error": "Modify access required"}, status_code=403)
        upload_dir = str(data.get("upload_dir", "") or "")
        filename = str(data.get("filename", "") or "")
        if not filename:
            return JSONResponse({"error": "filename required"}, status_code=400)
        if len(filename) > 255 or len(upload_dir) > REL_PATH_MAX_LEN:
            return JSONResponse({"error": "path too long"}, status_code=400)
        claims = BulkTicketClaims(
            op="PUT",
            username=username,
            size=size,
            exp=default_expiry(),
            upload_dir=upload_dir,
            filename=filename,
            remote_ip=_client_ip(request),
        )
    else:
        relpath = str(data.get("relpath", "") or "").strip().strip("/")
        if not relpath or len(relpath) > REL_PATH_MAX_LEN:
            return JSONResponse({"error": "relpath required"}, status_code=400)
        claims = BulkTicketClaims(
            op="GET",
            username=username,
            size=size,
            exp=default_expiry(),
            relpath=relpath,
            remote_ip=_client_ip(request),
        )

    ticket = mint_bulk_ticket(secret, claims)
    port = get_bulk_tcp_port()
    return JSONResponse(
        {
            "ticket": ticket,
            "port": port,
            "expires": claims.exp,
            "ttl": BULK_TICKET_TTL_SEC,
        }
    )


# ---------------------------------------------------------------------------
# Share update
# ---------------------------------------------------------------------------


class _ShareUpdateAdapter:
    def __init__(self, request: Request, user: dict[str, Any]):
        self.request = request
        self._user = user

    def get_current_user(self):
        return self._user

    def get_service(self, name: str):
        return get_service(self.request, name)

    def get_display_username(self) -> str:
        return username_from_user(self._user) or ""

    def is_admin_user(self) -> bool:
        return is_admin_user(self._user)

    def is_share_owner(self, share: dict) -> bool:
        creator = (share.get("created_by") or share.get("creator") or "").strip()
        username = username_from_user(self._user) or ""
        if creator and username and login_matches_share_creator_field(creator, username):
            return True
        return not creator and self.is_admin_user()

    def can_edit_share_paths(self, share: dict) -> bool:
        if self.is_share_owner(share):
            return True
        username = username_from_user(self._user) or ""
        return bool(username and username in (share.get("modify_users") or []))

    def can_manage_share_secrets(self, share: dict) -> bool:
        return self.is_share_owner(share)


async def share_update(request: Request) -> Response:
    user = require_user(request)
    if not isinstance(user, dict):
        return RedirectResponse("/login", status_code=302)

    if not is_feature_enabled("file_share", True):
        return JSONResponse({"error": FS_DISABLED_MSG}, status_code=403)

    if not has_modify_privileges(user):
        return JSONResponse({"error": "Modify access required"}, status_code=403)

    conn = db_conn(request)
    if conn is None:
        return JSONResponse({"error": "Database not available"}, status_code=503)

    try:
        raw = await request.body()
        if len(raw) > SHARE_JSON_BODY_MAX_BYTES:
            return JSONResponse({"error": "Request body too large"}, status_code=400)
        data = json.loads(raw.decode("utf-8") if raw else "{}")
    except json.JSONDecodeError:
        return JSONResponse({"error": "Invalid JSON"}, status_code=400)

    try:
        check_xsrf_token(request, data.get("_xsrf"))
    except PermissionError:
        return JSONResponse({"error": "XSRF validation failed"}, status_code=403)

    vserr = validate_share_update_struct(data)
    if vserr:
        return JSONResponse({"error": vserr}, status_code=400)

    adapter = _ShareUpdateAdapter(request, user)
    share_id, share_data, err_status, err_body = _validate_share_update_request(adapter, conn, data)
    if err_status is not None:
        return JSONResponse(err_body, status_code=err_status)

    data = _share_update_payload_for_user(adapter, share_data, data)
    if data is None:
        return JSONResponse({"error": "Access denied"}, status_code=403)

    saved_share_id: str | None = None
    saved_new_cloud_paths: list[str] = []
    try:
        response_data, err, new_cloud_paths = _execute_share_update(
            adapter, conn, share_id, share_data, data
        )
        saved_share_id = share_id
        saved_new_cloud_paths = new_cloud_paths or []
        if err is not None:
            return JSONResponse(err[1], status_code=err[0])
        return JSONResponse(response_data)
    except Exception:
        if saved_share_id and saved_new_cloud_paths:
            for rel_path in saved_new_cloud_paths:
                remove_cloud_file_if_exists(saved_share_id, rel_path)
            cleanup_share_cloud_dir_if_empty(saved_share_id)
        logger.exception("share update failed")
        return JSONResponse(
            {"error": "Failed to update share. Please try again."},
            status_code=500,
        )


# ---------------------------------------------------------------------------
# Admin user delete / reset password
# ---------------------------------------------------------------------------


async def admin_user_delete(request: Request) -> Response:
    try:
        require_admin(request)
    except PermissionError:
        return PlainTextResponse("Access denied", status_code=403)

    conn = db_conn(request)
    if conn is None:
        return PlainTextResponse("Database not available", status_code=503)

    form = await request.form()
    try:
        check_xsrf_token(request, str(form.get("_xsrf") or ""))
    except PermissionError:
        return PlainTextResponse("XSRF validation failed", status_code=403)

    try:
        user_id = int(str(form.get("user_id", "0")))
    except ValueError:
        return PlainTextResponse("Invalid user id", status_code=400)

    if user_id <= 0:
        return PlainTextResponse("Invalid user id", status_code=400)

    user_service = get_service(request, "user_service")
    if user_service is None:
        return PlainTextResponse("User service unavailable", status_code=503)

    deleted = user_service.delete_user(conn, user_id)
    if deleted:
        return RedirectResponse("/admin/users", status_code=302)
    return PlainTextResponse("User not found", status_code=404)


async def admin_user_reset_password(request: Request) -> Response:
    try:
        require_admin(request)
    except PermissionError:
        return PlainTextResponse("Access denied", status_code=403)

    conn = db_conn(request)
    if conn is None:
        return PlainTextResponse("Database not available", status_code=503)

    form = await request.form()
    try:
        check_xsrf_token(request, str(form.get("_xsrf") or ""))
    except PermissionError:
        return PlainTextResponse("XSRF validation failed", status_code=403)

    try:
        user_id = int(str(form.get("user_id", "0")))
    except ValueError:
        return PlainTextResponse("Invalid user id", status_code=400)

    if user_id <= 0:
        return PlainTextResponse("Invalid user id", status_code=400)

    user_service = get_service(request, "user_service")
    if user_service is None:
        return PlainTextResponse("User service unavailable", status_code=503)

    temp_password = secrets.token_urlsafe(32)
    if not user_service.update_user(
        conn,
        user_id,
        password=temp_password,
        must_change_password=True,
    ):
        return PlainTextResponse("Password reset failed", status_code=500)

    refreshed = user_service.list_users(conn)
    target = next((u for u in refreshed if u["id"] == user_id), None)
    if not target:
        return PlainTextResponse("User not found", status_code=404)

    audit = get_service(request, "audit_service")
    if audit:
        try:
            audit.log(
                conn,
                "admin_password_reset",
                username=target["username"],
                ip=_client_ip(request),
            )
        except Exception:
            logger.debug("audit admin_password_reset failed", exc_info=True)

    return render_html(
        request,
        "admin_users.html",
        users=refreshed,
        reset_password_plain=temp_password,
        reset_for_username=target["username"],
    )


routes = [
    Route("/api/webauthn/status", webauthn_status, methods=["GET"]),
    Route("/api/webauthn/register/options", webauthn_register_options, methods=["POST"]),
    Route("/api/webauthn/register/verify", webauthn_register_verify, methods=["POST"]),
    Route("/api/webauthn/auth/options", webauthn_auth_options, methods=["POST"]),
    Route("/api/webauthn/auth/verify", webauthn_auth_verify, methods=["POST"]),
    Route("/api/webauthn/credentials/{cred_id}", webauthn_credential_delete, methods=["DELETE"]),
    Route("/api/cloud/providers", cloud_providers, methods=["GET"]),
    Route("/api/cloud/{provider_name}/files", cloud_files, methods=["GET"]),
    Route("/api/cloud/{provider_name}/download", cloud_download, methods=["GET"]),
    Route("/edit/{path:path}", edit_view, methods=["GET"]),
    Route("/edit", edit_save, methods=["POST"]),
    Route("/tagged/{tag_name}", tagged_files, methods=["GET"]),
    Route("/api/bulk/ticket", bulk_ticket_get, methods=["GET"]),
    Route("/api/bulk/ticket", bulk_ticket_post, methods=["POST"]),
    Route("/share/update", share_update, methods=["POST"]),
    Route("/admin/users/delete", admin_user_delete, methods=["POST"]),
    Route("/admin/users/reset-password", admin_user_reset_password, methods=["POST"]),
]

__all__ = ["routes"]
