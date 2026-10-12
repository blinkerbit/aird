"""
Aird transfer microservice (socketify only).

Owns: POST /upload, GET /files/*?download=1, and /health.
Authn: Caddy (X-Aird-User). Authz: user-root confinement before disk I/O.
"""

from __future__ import annotations

import logging
import os
import re
from typing import Any
from urllib.parse import unquote

from socketify import App

from aird.ms.common import (
    health_json_bytes,
    identity_from_headers,
    mesh_secret_ok,
    require_mesh_configuration,
    require_user,
)
from aird.core.security import is_within_root
from aird.core.transfer_engine import (
    _plain_end,
    stream_download_chunks,
    stream_upload_on_data,
    stream_upload_range_on_data,
)
logger = logging.getLogger(__name__)

SERVICE = "aird-transfer"
_DOWNLOAD_RE = re.compile(r"^/files/(.*)$")


def _user_root(username: str) -> str:
    """Resolve storage root for an authenticated user."""
    from aird.ms.ctx import user_root_for

    return user_root_for({"username": username, "role": "user"})


def _user_mounts(username: str) -> list[dict]:
    from aird.core.browse_paths import lazy_runtime_db, mounts_for_username

    return mounts_for_username(lazy_runtime_db(), username)


def _safe_destination(username: str, upload_dir: str, filename: str) -> tuple[str, str]:
    from aird.core.browse_paths import resolve_rel, write_blocked_reason

    if not filename or filename != os.path.basename(filename) or filename in (".", ".."):
        raise ValueError("Invalid filename")
    user_root = _user_root(username)
    mounts = _user_mounts(username)
    if write_blocked_reason(upload_dir, mounts):
        raise ValueError("Upload directory is read-only")
    dest_dir, confine = resolve_rel(user_root, upload_dir, mounts)
    if not dest_dir or not confine:
        raise ValueError("Invalid upload directory")
    dest_dir = os.path.realpath(dest_dir)
    confine = os.path.realpath(confine)
    if not is_within_root(dest_dir, confine):
        raise ValueError("Invalid upload directory")
    final = os.path.realpath(os.path.join(dest_dir, filename))
    if not is_within_root(final, dest_dir):
        raise ValueError("Invalid filename")
    return dest_dir, final


def _headers_dict(req: Any) -> dict[str, str]:
    out: dict[str, str] = {}
    try:
        raw = req.get_headers() or {}
        if isinstance(raw, dict):
            for k, v in raw.items():
                out[str(k).lower()] = str(v)
    except Exception:
        pass
    for name in (
        "cookie",
        "content-length",
        "content-type",
        "x-aird-user",
        "x-aird-role",
        "x-aird-mesh",
        "x-upload-dir",
        "x-upload-filename",
        "x-upload-session",
        "x-upload-offset",
        "x-upload-total",
        "x-upload-complete",
        "x-aird-upload-ticket",
    ):
        try:
            val = req.get_header(name)
            if val:
                out[name] = val
        except Exception:
            pass
    return out


def create_transfer_app() -> App:
    app = App()

    async def health(res, req):
        body, code = health_json_bytes(
            SERVICE,
            ready=True,
            checks={"socketify": "ok", "role": "transfer"},
        )

        def _send(r):
            r.write_status(code)
            r.write_header("Content-Type", "application/json; charset=UTF-8")
            r.write_header("Cache-Control", "no-store")
            r.write_header("Content-Length", str(len(body)))
            r.end(body)

        try:
            res.cork(_send)
        except Exception:
            _send(res)

    async def upload(res, req):
        req.preserve()
        headers = _headers_dict(req)
        if not mesh_secret_ok(headers):
            _plain_end(res, 401, "mesh")
            return
        try:
            username = require_user(headers)
        except PermissionError:
            if os.environ.get("AIRD_DEV_OPEN_AUTH", "").strip() == "1":
                username = "dev"
            else:
                _plain_end(res, 401, "missing X-Aird-User")
                return

        user_root = _user_root(username)
        filename = unquote(headers.get("x-upload-filename") or "").strip()
        upload_dir = unquote(headers.get("x-upload-dir") or "").strip()
        if not filename:
            _plain_end(res, 400, "Missing X-Upload-Filename")
            return
        try:
            _safe_destination(username, upload_dir, filename)
        except ValueError as exc:
            _plain_end(res, 400, str(exc))
            return

        from aird.core import upload_sessions

        session_id = (headers.get("x-upload-session") or "").strip()
        complete = (headers.get("x-upload-complete") or "").lower() in ("1", "true", "yes")

        if complete and upload_sessions.valid_session_id(session_id):
            sess = upload_sessions.pop_session(user_root, session_id)
            if sess is None:
                _plain_end(res, 404, "Upload session not found")
                return
            upload_sessions.close_session_fd(sess)
            try:
                dest_dir, final = _safe_destination(
                    username, sess.upload_dir, sess.filename
                )
            except ValueError as exc:
                _plain_end(res, 400, str(exc))
                return
            os.makedirs(dest_dir, exist_ok=True)
            try:
                os.replace(sess.temp_path, final)
            except OSError:
                logger.exception("finalize failed")
                _plain_end(res, 500, "Finalize failed")
                return
            _plain_end(res, 200, "Upload successful")
            return

        try:
            expected = int(headers.get("content-length") or 0)
        except ValueError:
            expected = 0
        if expected <= 0 and not complete:
            _plain_end(res, 400, "Content-Length required")
            return

        offset_raw = headers.get("x-upload-offset") or ""
        total_raw = headers.get("x-upload-total") or ""
        if (
            upload_sessions.valid_session_id(session_id)
            and offset_raw != ""
            and total_raw != ""
        ):
            try:
                offset = int(offset_raw)
                total = int(total_raw)
            except ValueError:
                _plain_end(res, 400, "Invalid upload range")
                return
            try:
                sess = upload_sessions.open_or_get_session(
                    session_id=session_id,
                    user_root=user_root,
                    upload_dir=upload_dir,
                    filename=filename,
                    total=total,
                )
            except ValueError as exc:
                _plain_end(res, 400, str(exc) or "Bad upload session")
                return

            async def _chunk_done(nbytes: int):
                _plain_end(res, 200, f"OK {nbytes}")

            await stream_upload_range_on_data(
                res,
                write_chunk=lambda abs_off, data: upload_sessions.write_at_offset(
                    sess, abs_off, data
                ),
                file_offset=offset,
                expected_bytes=expected,
                on_complete=_chunk_done,
                on_error=lambda status, message: _plain_end(res, status, message),
            )
            return

        async def _finalize(temp_path: str, nbytes: int):
            try:
                dest_dir, final = _safe_destination(
                    username, upload_dir, filename
                )
            except ValueError as exc:
                _plain_end(res, 400, str(exc))
                return
            os.makedirs(dest_dir, exist_ok=True)
            try:
                os.replace(temp_path, final)
            except OSError:
                try:
                    if os.path.exists(temp_path):
                        os.remove(temp_path)
                except OSError:
                    pass
                _plain_end(res, 500, "Finalize failed")
                return
            _plain_end(res, 200, f"Upload successful ({nbytes} bytes)")

        await stream_upload_on_data(
            res,
            dest_dir=user_root,
            expected_bytes=expected,
            on_complete=_finalize,
            on_error=lambda status, message: _plain_end(res, status, message),
        )

    async def download(res, req):
        req.preserve()
        headers = _headers_dict(req)
        if not mesh_secret_ok(headers):
            _plain_end(res, 401, "mesh")
            return
        try:
            username = require_user(headers)
        except PermissionError:
            if os.environ.get("AIRD_DEV_OPEN_AUTH", "").strip() == "1":
                username = "dev"
            else:
                _plain_end(res, 401, "missing X-Aird-User")
                return

        path = req.get_url() or "/"
        m = _DOWNLOAD_RE.match(path)
        if not m:
            _plain_end(res, 404, "Not Found")
            return
        rel = unquote(m.group(1) or "").lstrip("/")
        from aird.core.browse_paths import resolve_rel

        user_root = _user_root(username)
        abs_path, confine = resolve_rel(user_root, rel, _user_mounts(username))
        if not abs_path or not confine:
            _plain_end(res, 403, "Forbidden")
            return
        abs_path = os.path.realpath(abs_path)
        confine = os.path.realpath(confine)
        if not is_within_root(abs_path, confine):
            _plain_end(res, 403, "Forbidden")
            return
        if not os.path.isfile(abs_path):
            _plain_end(res, 404, "Not Found")
            return
        await stream_download_chunks(
            res,
            req,
            abs_path,
            filename=os.path.basename(abs_path),
            as_attachment=True,
        )

    async def session_prepare(res, req):
        """Prepare a parallel upload session owned entirely by socketify."""
        import json
        import secrets as pysecrets

        req.preserve()
        headers = _headers_dict(req)
        if not mesh_secret_ok(headers):
            _plain_end(res, 401, "mesh")
            return
        try:
            username = require_user(headers)
        except PermissionError:
            if os.environ.get("AIRD_DEV_OPEN_AUTH", "").strip() == "1":
                username = "dev"
            else:
                _plain_end(res, 401, "missing X-Aird-User")
                return

        # Drain tiny JSON body via get_data if available
        try:
            raw = await res.get_data()
            data = json.loads(raw.getvalue().decode("utf-8") or "{}")
        except Exception:
            data = {}

        from aird.core import upload_sessions
        session_id = str(data.get("session") or pysecrets.token_hex(16))
        filename = unquote(str(data.get("filename") or "").strip())
        upload_dir = unquote(str(data.get("upload_dir") or "").strip())
        try:
            total = int(data.get("total") or 0)
        except (TypeError, ValueError):
            total = 0
        if not filename or total <= 0:
            _plain_end(res, 400, "Invalid session metadata")
            return
        user_root = _user_root(username)
        try:
            _safe_destination(username, upload_dir, filename)
        except ValueError as exc:
            _plain_end(res, 400, str(exc))
            return
        try:
            sess = upload_sessions.open_or_get_session(
                session_id=session_id,
                user_root=user_root,
                upload_dir=upload_dir,
                filename=filename,
                total=total,
            )
        except ValueError as exc:
            _plain_end(res, 400, str(exc) or "Bad session")
            return
        body = json.dumps(
            {
                "session": sess.session_id,
                "total": sess.total,
            }
        ).encode()

        def _send(r):
            r.write_status(200)
            r.write_header("Content-Type", "application/json")
            r.write_header("Content-Length", str(len(body)))
            r.end(body)

        try:
            res.cork(_send)
        except Exception:
            _send(res)

    app.get("/health", health)
    app.get("/healthz", health)
    app.get("/ready", health)
    app.post("/upload", upload)
    app.post("/api/upload/session", session_prepare)
    app.get("/files/*", download)
    return app


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    require_mesh_configuration(SERVICE)
    host = os.environ.get("AIRD_TRANSFER_HOST", "127.0.0.1")
    port = int(os.environ.get("AIRD_TRANSFER_PORT_HTTP", "8002"))
    app = create_transfer_app()
    logger.info("aird-transfer (socketify) on %s:%s", host, port)

    def _on_listen(config):
        if config:
            logger.info("transfer listening on %s:%s", host, config.port)

    try:
        from socketify import AppListenOptions

        app.listen(AppListenOptions(port=port, host=host), _on_listen)
    except Exception:
        app.listen(port, _on_listen)
    app.run()


if __name__ == "__main__":
    main()
