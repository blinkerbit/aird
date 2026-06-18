"""WebAuthn passkey registration and authentication (optional feature)."""

from __future__ import annotations

import base64
import json
import logging

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
from aird.handlers.constants import FILES_BASE_URL
from aird.utils.util import is_feature_enabled

logger = logging.getLogger(__name__)

_PASSKEY_UNAVAILABLE = "Passkey sign-in is not available for this account."

PURPOSE_REGISTER = "register"
PURPOSE_AUTH = "auth"


def _webauthn_enabled() -> bool:
    return is_feature_enabled("webauthn", False)


class WebAuthnStatusHandler(BaseHandler):
    """Public status for client feature detection."""

    def get(self):
        if not _webauthn_enabled():
            self.set_status(404)
            self.finish()
            return
        rp_id, _, _ = resolve_webauthn_config(self)
        self.set_header("Content-Type", "application/json")
        self.write(json.dumps({"enabled": True, "rpId": rp_id}))


class WebAuthnRegisterOptionsHandler(BaseHandler):
    @tornado.web.authenticated
    async def post(self):
        if not _webauthn_enabled():
            self.set_status(404)
            self.finish()
            return
        user = self.current_user
        if not isinstance(user, dict) or not user.get("username"):
            self.set_status(403)
            self.write(json.dumps({"error": "Passkeys require a full user account."}))
            return
        username = user["username"]
        if username in ("token_user", "admin_token", "token_authenticated", "admin_token_authenticated"):
            self.set_status(403)
            self.write(json.dumps({"error": "Passkeys are not available for token sessions."}))
            return

        db_conn = self.db_conn
        if not db_conn:
            self.set_status(503)
            self.write(json.dumps({"error": "Database unavailable."}))
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
            self.set_status(500)
            self.write(json.dumps({"error": "Could not store challenge."}))
            return

        prf_salt = webauthn_db.ensure_prf_salt(db_conn, username)
        payload = json.loads(options_to_json(options))
        payload["extensions"] = {"prf": {}}
        if prf_salt:
            payload["prfSalt"] = prf_salt
        self.set_header("Content-Type", "application/json")
        self.write(json.dumps(payload))


class WebAuthnRegisterVerifyHandler(BaseHandler):
    @tornado.web.authenticated
    async def post(self):
        if not _webauthn_enabled():
            self.set_status(404)
            self.finish()
            return
        user = self.current_user
        if not isinstance(user, dict) or not user.get("username"):
            self.set_status(403)
            self.write(json.dumps({"error": "Forbidden"}))
            return
        username = user["username"]

        db_conn = self.db_conn
        if not db_conn:
            self.set_status(503)
            self.write(json.dumps({"error": "Database unavailable."}))
            return

        try:
            body = json.loads(self.request.body or b"{}")
        except json.JSONDecodeError:
            self.set_status(400)
            self.write(json.dumps({"error": "Invalid JSON."}))
            return

        rp_id, _, origins = resolve_webauthn_config(self)
        try:
            credential = parse_registration_credential_json(json.dumps(body.get("credential", body)))
        except Exception:
            self.set_status(400)
            self.write(json.dumps({"error": "Invalid credential."}))
            return

        try:
            client_data = json.loads(credential.response.client_data_json.decode("utf-8"))
            challenge_b64 = client_data.get("challenge", "")
            challenge_bytes = base64.urlsafe_b64decode(_pad_b64(challenge_b64))
        except Exception:
            self.set_status(400)
            self.write(json.dumps({"error": "Invalid client data."}))
            return

        challenge_user = webauthn_db.consume_challenge(db_conn, challenge_bytes, PURPOSE_REGISTER)
        if challenge_user is None or challenge_user != username:
            self.set_status(400)
            self.write(json.dumps({"error": "Challenge expired or invalid."}))
            return

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
            self.set_status(400)
            self.write(json.dumps({"error": "Passkey registration failed."}))
            return

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
            self.set_status(500)
            self.write(json.dumps({"error": "Could not save passkey."}))
            return

        self.set_header("Content-Type", "application/json")
        self.write(json.dumps({"ok": True, "id": cred_id_b64}))


class WebAuthnAuthOptionsHandler(BaseHandler):
    async def post(self):
        if not _webauthn_enabled():
            self.set_status(404)
            self.finish()
            return

        db_conn = self.db_conn
        if not db_conn:
            self.set_status(503)
            self.write(json.dumps({"error": "Database unavailable."}))
            return

        try:
            body = json.loads(self.request.body or b"{}")
        except json.JSONDecodeError:
            self.set_status(400)
            self.write(json.dumps({"error": "Invalid JSON."}))
            return

        username = (body.get("username") or "").strip()
        if not username:
            self.set_status(400)
            self.write(json.dumps({"error": "Username is required."}))
            return

        creds = webauthn_db.list_credentials(db_conn, username)
        if not creds:
            self.set_status(400)
            self.write(json.dumps({"error": _PASSKEY_UNAVAILABLE}))
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
            self.set_status(500)
            self.write(json.dumps({"error": "Could not store challenge."}))
            return

        self.set_header("Content-Type", "application/json")
        self.write(json.dumps(json.loads(options_to_json(options))))


class WebAuthnAuthVerifyHandler(BaseHandler):
    async def post(self):
        if not _webauthn_enabled():
            self.set_status(404)
            self.finish()
            return

        if not check_login_rate_limit(self.request.remote_ip):
            self.set_status(429)
            self.write(json.dumps({"error": "Too many login attempts."}))
            return

        db_conn = self.db_conn
        if not db_conn:
            self.set_status(503)
            self.write(json.dumps({"error": "Database unavailable."}))
            return

        try:
            body = json.loads(self.request.body or b"{}")
        except json.JSONDecodeError:
            self.set_status(400)
            self.write(json.dumps({"error": "Invalid JSON."}))
            return

        try:
            credential = parse_authentication_credential_json(json.dumps(body.get("credential", body)))
        except Exception:
            self.set_status(400)
            self.write(json.dumps({"error": "Invalid credential."}))
            return

        cred_id_b64 = webauthn_db.credential_id_to_b64(credential.raw_id)
        stored = webauthn_db.get_credential_by_credential_id(db_conn, cred_id_b64)
        if not stored:
            self.set_status(400)
            self.write(json.dumps({"error": "Unknown passkey."}))
            return

        try:
            client_data = json.loads(credential.response.client_data_json.decode("utf-8"))
            challenge_b64 = client_data.get("challenge", "")
            challenge_bytes = base64.urlsafe_b64decode(_pad_b64(challenge_b64))
        except Exception:
            self.set_status(400)
            self.write(json.dumps({"error": "Invalid client data."}))
            return

        challenge_user = webauthn_db.consume_challenge(db_conn, challenge_bytes, PURPOSE_AUTH)
        if challenge_user is None or challenge_user != stored["username"]:
            self.set_status(400)
            self.write(json.dumps({"error": "Challenge expired or invalid."}))
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
            self.set_status(400)
            self.write(json.dumps({"error": "Passkey authentication failed."}))
            return

        webauthn_db.update_sign_count(db_conn, stored["id"], verified.new_sign_count)

        user_service = self.get_service("user_service")
        user = user_service.get_user(db_conn, stored["username"])
        if not user:
            self.set_status(403)
            self.write(json.dumps({"error": "User account not found."}))
            return

        user_role = user.get("role", "user")
        _apply_session_cookies(self, stored["username"], user_role)
        self.get_service("audit_service").log(
            db_conn, "login", username=stored["username"], ip=self.request.remote_ip
        )

        next_url = (body.get("next") or FILES_BASE_URL).strip()
        if not next_url.startswith("/") or next_url.startswith("//"):
            next_url = FILES_BASE_URL

        self.set_header("Content-Type", "application/json")
        self.write(json.dumps({"ok": True, "redirect": next_url}))


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
        db_conn = self.db_conn
        if not db_conn:
            self.set_status(503)
            self.finish()
            return
        try:
            cred_db_id = int(cred_id)
        except (TypeError, ValueError):
            self.set_status(400)
            self.write(json.dumps({"error": "Invalid credential id."}))
            return
        if webauthn_db.delete_credential(db_conn, cred_db_id, username):
            self.set_header("Content-Type", "application/json")
            self.write(json.dumps({"ok": True}))
        else:
            self.set_status(404)
            self.write(json.dumps({"error": "Passkey not found."}))


def _pad_b64(value: str) -> str:
    s = value.replace("-", "+").replace("_", "/")
    pad = (4 - len(s) % 4) % 4
    return s + ("=" * pad)
