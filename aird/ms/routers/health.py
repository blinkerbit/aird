"""Health probes, PWA manifest, and service-worker routes."""

from __future__ import annotations

import logging
from pathlib import Path

from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse
from starlette.routing import Route

import aird.constants as constants_module
from aird.ms.render import static_dir

logger = logging.getLogger(__name__)


async def health(_request):
    status = {"status": "ok"}
    healthy = True

    if constants_module.DB_CONN is not None:
        try:
            constants_module.DB_CONN.execute("SELECT 1").fetchone()
            status["db"] = "ok"
        except Exception as exc:
            logger.warning("Health check DB probe failed: %s", exc)
            status["db"] = "error"
            healthy = False
    else:
        status["db"] = "not_configured"

    if not healthy:
        status["status"] = "error"
        return JSONResponse(
            status,
            status_code=503,
            headers={"Cache-Control": "no-store"},
        )

    return JSONResponse(status, headers={"Cache-Control": "no-store"})


def _sw_response() -> FileResponse:
    sw_path = Path(static_dir()) / "js" / "sw.js"
    return FileResponse(
        sw_path,
        media_type="application/javascript; charset=utf-8",
        headers={
            "Service-Worker-Allowed": "/",
            "Cache-Control": "no-store, must-revalidate",
            "Pragma": "no-cache",
        },
    )


async def service_worker(_request):
    return _sw_response()


async def manifest(_request):
    path = Path(static_dir()) / "manifest.webmanifest"
    return FileResponse(
        path,
        media_type="application/manifest+json; charset=utf-8",
        headers={"Cache-Control": "no-cache"},
    )


async def vapid_public_key(_request: Request):
    try:
        from aird.pwa.vapid import vapid_public_key as get_key

        return JSONResponse({"publicKey": get_key()})
    except Exception as exc:
        logger.warning("VAPID public key unavailable: %s", exc)
        return JSONResponse(
            {"error": "Push notifications are not configured."},
            status_code=503,
        )


def _username_from_request(request: Request) -> str | None:
    from aird.ms.ctx import current_user

    user = current_user(request)
    if isinstance(user, dict):
        name = user.get("username") or user.get("name")
        return str(name) if name else None
    if isinstance(user, str) and user:
        return user
    return None


async def push_subscribe(request: Request):
    username = _username_from_request(request)
    if not username:
        return JSONResponse({"error": "Login required"}, status_code=403)
    conn = constants_module.DB_CONN
    if not conn:
        return JSONResponse({"error": "Database unavailable"}, status_code=503)
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "Invalid JSON"}, status_code=400)
    endpoint = (body.get("endpoint") or "").strip()
    keys = body.get("keys") or {}
    p256dh = (keys.get("p256dh") or "").strip()
    auth = (keys.get("auth") or "").strip()
    if not endpoint or not p256dh or not auth:
        return JSONResponse({"error": "Invalid subscription"}, status_code=400)
    from aird.pwa import store as push_store

    push_store.upsert_subscription(
        conn,
        username=username,
        endpoint=endpoint,
        p256dh=p256dh,
        auth=auth,
        user_agent=request.headers.get("user-agent"),
    )
    return JSONResponse({"ok": True})


async def push_unsubscribe(request: Request):
    username = _username_from_request(request)
    if not username:
        return JSONResponse({"error": "Login required"}, status_code=403)
    conn = constants_module.DB_CONN
    if not conn:
        return JSONResponse({"error": "Database unavailable"}, status_code=503)
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "Invalid JSON"}, status_code=400)
    endpoint = (body.get("endpoint") or "").strip()
    if not endpoint:
        return JSONResponse({"error": "endpoint required"}, status_code=400)
    from aird.pwa import store as push_store

    push_store.delete_subscription(conn, endpoint=endpoint, username=username)
    return JSONResponse({"ok": True})


routes = [
    Route("/health", health, methods=["GET"]),
    Route("/healthz", health, methods=["GET"]),
    Route("/ready", health, methods=["GET"]),
    Route("/sw.js", service_worker, methods=["GET"]),
    Route("/sw-transfer.js", service_worker, methods=["GET"]),
    Route("/manifest.webmanifest", manifest, methods=["GET"]),
    Route("/api/pwa/vapid-public-key", vapid_public_key, methods=["GET"]),
    Route("/api/pwa/push-subscribe", push_subscribe, methods=["POST"]),
    Route("/api/pwa/push-unsubscribe", push_unsubscribe, methods=["POST"]),
]
