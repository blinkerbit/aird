"""Authentication microservice (Granian/Starlette, port 8000)."""

import os

from aird.ms.jwt_auth import routes as jwt_routes
from aird.ms.routers.auth import routes as auth_routes
from aird.ms.routers.auth_verify import routes as verify_routes
from aird.ms.routers.extra_api import routes as extra_routes
from aird.ms.routers.health import routes as health_routes
from aird.ms.service_runtime import create_service_app, select_routes, serve

SERVICE = "aird-auth"

routes = [
    *health_routes,
    *verify_routes,
    *auth_routes,
    *jwt_routes,
    *select_routes(extra_routes, prefixes=("/api/webauthn",)),
]

app = create_service_app(SERVICE, routes)


def main() -> None:
    if (
        os.environ.get("AIRD_DEV_OPEN_AUTH", "").strip() != "1"
        and not os.environ.get("AIRD_JWT_SECRET", "").strip()
    ):
        raise SystemExit("AIRD_JWT_SECRET is required outside development")
    serve(__name__, SERVICE, "AIRD_AUTH_HOST", "AIRD_AUTH_PORT", 8000)


if __name__ == "__main__":
    main()
