"""WebAuthn passkey registration and authentication (optional feature)."""

from __future__ import annotations

import base64
import json
import logging
from typing import Any

import tornado.web
from webauthn import (
    generate_authentication_options,
    generate_registration_options,
    options_to_json,
    verify_authentication_response,
    verify_registration_response,
)
from webauthn.helpers import parse_authentication_credential_json, parse_registration_credential_json
from webauthn.helpers.cose import COSEAlgorithmIdentifier
from webauthn.helpers.structs import (
    AuthenticatorSelectionCriteria,
    PublicKeyCredentialDescriptor,
    ResidentKeyRequirement,
    UserVerificationRequirement,
)

from aird.core.webauthn_config import resolve_webauthn_config
from aird.db import webauthn as webauthn_db
from aird.handlers.auth_handlers import _apply_session_cookies, check_login_rate_limit
from aird.handlers.base_handler import BaseHandler
from aird.handlers.constants import (
    CONTENT_TYPE_JSON,
    DB_UNAVAILABLE_MSG,
    FILES_BASE_URL,
    INVALID_JSON_MSG,
)
from aird.utils.util import is_feature_enabled

logger = logging.getLogger(__name__)

_PASSKEY_UNAVAILABLE = "Passkey sign-in is not available for this account."

PURPOSE_REGISTER = "register"
PURPOSE_AUTH = "auth"


def _webauthn_enabled() -> bool:
    return is_feature_enabled("webauthn", False)


def _write_json(handler: BaseHandler, payload: dict[str, Any], *, status: int | None = None) -> None:
    if status is not None:
        handler.set_status(status)
    handler.set_header("Content-Type", CONTENT_TYPE_JSON)
    handler.write(json.dumps(payload))


def _write_json_error(handler: BaseHandler, status: int, error: str) -> None:
    _write_json(handler, {"error": error}, status=status)


def _require_db_conn(handler: BaseHandler):
    db_conn = handler.db_conn
    if not db_conn:
        _write_json_error(handler, 503, DB_UNAVAILABLE_MSG)
        return None
    return db_conn


def _parse_json_body(handler: BaseHandler) -> dict[str, Any] | None:
    try:
        return json.loads(handler.request.body or b"{}")
    except json.JSONDecodeError:
        _write_json_error(handler, 400, INVALID_JSON_MSG)
        return None


def _decode_challenge_from_credential(credential) -> bytes | None:
    try:
        client_data = json.loads(credential.response.client_data_json.decode("utf-8"))
        challenge_b64 = client_data.get("challenge", "")
        return base64.urlsafe_b64decode(_pad_b64(challenge_b64))
    except Exception:
        return None


class WebAuthnStatusHandler(BaseHandler):
    """Public status for client feature detection."""

    def get(self):
        if not _webauthn_enabled():
            self.set_status(404)
            self.finish()
            return
        rp_id, _, _ = resolve_webauthn_config(self)
        _write_json(self, {"enabled": True, "rpId": rp_id})


class WebAuthnRegisterOptionsHandler(BaseHandler):
    @tornado.web.authenticated
    async def post(self):
        if not _webauthn_enabled():
            self.set_status(404)
            self.finish()
            return
        user = self.current_user
        if not isinstance(user, dict) or not user.get("username"):
            _write_json_error(self, 403, "Passkeys require a full user account.")
            return
        username = user["username"]
        if username in ("token_user", "admin_token", "token_authenticated", "admin_token_authenticated"):
            _write_json_error(self, 403, "Passkeys are not available for token sessions.")
            return

        db_conn = _require_db_conn(self)
        if not db_conn:
            return

        rp_id, rp_name, _ = resolve_webauthn_config(self)
        existing = webauthn_db.list_credentials(db_conn, username)
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
        if not webauthn_db.store_challenge(db_conn, options.challenge, PURPOSE_REGISTER, username):
            _write_json_error(self, 500, "Could not store challenge.")
            return

        prf_salt = webauthn_db.ensure_prf_salt(db_conn, username)
        payload = json.loads(options_to_json(options))
        payload["extensions"] = {"prf": {}}
        if prf_salt:
            payload["prfSalt"] = prf_salt
        _write_json(self, payload)


def _parse_registration_credential(body: dict[str, Any]):
    try:
        return parse_registration_credential_json(json.dumps(body.get("credential", body)))
    except Exception:
        return None


def _verify_registration(db_conn, username: str, body: dict[str, Any], credential, challenge_bytes, rp_id, origins):
    challenge_user = webauthn_db.consume_challenge(db_conn, challenge_bytes, PURPOSE_REGISTER)
    if challenge_user is None or challenge_user != username:
        return None, "Challenge expired or invalid."

    try:
        verified = verify_registration_response(
            credential=credential,
            expected_challenge=challenge_bytes,
            expected_rp_id=rp_id,
            expected_origin=origins,
            require_user_verification=False,
        )
    except Exception as exc:
        logger.info("webauthn register verify failed: %s", exc)
        return None, "Passkey registration failed."

    prf_capable = bool(body.get("prfCapable"))
    cred_id_b64 = webauthn_db.credential_id_to_b64(verified.credential_id)
    transports = ",".join(body.get("transports") or []) or None
    nickname = (body.get("nickname") or "").strip() or None
    aaguid = str(verified.aaguid) if verified.aaguid else None

    if not webauthn_db.create_credential(
        db_conn,
        username=username,
        credential_id=cred_id_b64,
        public_key=verified.credential_public_key,
        sign_count=verified.sign_count,
        transports=transports,
        aaguid=aaguid,
        prf_capable=prf_capable,
        nickname=nickname,
    ):
        return None, "Could not save passkey."

    return cred_id_b64, None


class WebAuthnRegisterVerifyHandler(BaseHandler):
    @tornado.web.authenticated
    async def post(self):
        if not _webauthn_enabled():
            self.set_status(404)
            self.finish()
            return
        user = self.current_user
        if not isinstance(user, dict) or not user.get("username"):
            _write_json_error(self, 403, "Forbidden")
            return
        username = user["username"]

        db_conn = _require_db_conn(self)
        if not db_conn:
            return

        body = _parse_json_body(self)
        if body is None:
            return

        credential = _parse_registration_credential(body)
        if credential is None:
            _write_json_error(self, 400, "Invalid credential.")
            return

        challenge_bytes = _decode_challenge_from_credential(credential)
        if challenge_bytes is None:
            _write_json_error(self, 400, "Invalid client data.")
            return

        rp_id, _, origins = resolve_webauthn_config(self)
        cred_id_b64, err = _verify_registration(
            db_conn, username, body, credential, challenge_bytes, rp_id, origins
        )
        if err:
            _write_json_error(self, 400, err)
            return

        _write_json(self, {"ok": True, "id": cred_id_b64})


class WebAuthnAuthOptionsHandler(BaseHandler):
    async def post(self):
        if not _webauthn_enabled():
            self.set_status(404)
            self.finish()
            return

        db_conn = _require_db_conn(self)
        if not db_conn:
            return

        body = _parse_json_body(self)
        if body is None:
            return

        username = (body.get("username") or "").strip()
        if not username:
            _write_json_error(self, 400, "Username is required.")
            return

        creds = webauthn_db.list_credentials(db_conn, username)
        if not creds:
            _write_json_error(self, 400, _PASSKEY_UNAVAILABLE)
            return

        rp_id, _, _ = resolve_webauthn_config(self)
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
        if not webauthn_db.store_challenge(db_conn, options.challenge, PURPOSE_AUTH, username):
            _write_json_error(self, 500, "Could not store challenge.")
            return

        _write_json(self, json.loads(options_to_json(options)))


class WebAuthnAuthVerifyHandler(BaseHandler):
    async def post(self):
        if not _webauthn_enabled():
            self.set_status(404)
            self.finish()
            return

        if not check_login_rate_limit(self.request.remote_ip):
            _write_json_error(self, 429, "Too many login attempts.")
            return

        db_conn = _require_db_conn(self)
        if not db_conn:
            return

        body = _parse_json_body(self)
        if body is None:
            return

        try:
            credential = parse_authentication_credential_json(json.dumps(body.get("credential", body)))
        except Exception:
            _write_json_error(self, 400, "Invalid credential.")
            return

        cred_id_b64 = webauthn_db.credential_id_to_b64(credential.raw_id)
        stored = webauthn_db.get_credential_by_credential_id(db_conn, cred_id_b64)
        if not stored:
            _write_json_error(self, 400, "Unknown passkey.")
            return

        challenge_bytes = _decode_challenge_from_credential(credential)
        if challenge_bytes is None:
            _write_json_error(self, 400, "Invalid client data.")
            return

        challenge_user = webauthn_db.consume_challenge(db_conn, challenge_bytes, PURPOSE_AUTH)
        if challenge_user is None or challenge_user != stored["username"]:
            _write_json_error(self, 400, "Challenge expired or invalid.")
            return

        rp_id, _, origins = resolve_webauthn_config(self)
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
            _write_json_error(self, 400, "Passkey authentication failed.")
            return

        webauthn_db.update_sign_count(db_conn, stored["id"], verified.new_sign_count)

        user_service = self.get_service("user_service")
        user = user_service.get_user(db_conn, stored["username"])
        if not user:
            _write_json_error(self, 403, "User account not found.")
            return

        user_role = user.get("role", "user")
        _apply_session_cookies(self, stored["username"], user_role)
        self.get_service("audit_service").log(
            db_conn, "login", username=stored["username"], ip=self.request.remote_ip
        )

        next_url = (body.get("next") or FILES_BASE_URL).strip()
        if not next_url.startswith("/") or next_url.startswith("//"):
            next_url = FILES_BASE_URL

        _write_json(self, {"ok": True, "redirect": next_url})


class WebAuthnCredentialDeleteHandler(BaseHandler):
    @tornado.web.authenticated
    async def delete(self, cred_id):
        if not _webauthn_enabled():
            self.set_status(404)
            self.finish()
            return
        user = self.current_user
        if not isinstance(user, dict) or not user.get("username"):
            self.set_status(403)
            self.finish()
            return
        username = user["username"]
        db_conn = _require_db_conn(self)
        if not db_conn:
            return
        try:
            cred_db_id = int(cred_id)
        except (TypeError, ValueError):
            _write_json_error(self, 400, "Invalid credential id.")
            return
        if webauthn_db.delete_credential(db_conn, cred_db_id, username):
            _write_json(self, {"ok": True})
        else:
            _write_json_error(self, 404, "Passkey not found.")


def _pad_b64(value: str) -> str:
    s = value.replace("-", "+").replace("_", "/")
    pad = (4 - len(s) % 4) % 4
    return s + ("=" * pad)
