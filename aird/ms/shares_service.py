"""Sharing microservice (Granian/Starlette, port 8005)."""

from aird.ms.routers.extra_api import routes as extra_routes
from aird.ms.routers.health import routes as health_routes
from aird.ms.routers.shares import routes as share_routes
from aird.ms.service_runtime import create_service_app, select_routes, serve

SERVICE = "aird-shares"

routes = [
    *health_routes,
    *share_routes,
    *select_routes(extra_routes, exact=("/share/update",)),
]

app = create_service_app(SERVICE, routes)


def main() -> None:
    serve(__name__, SERVICE, "AIRD_SHARES_HOST", "AIRD_SHARES_PORT", 8005)


if __name__ == "__main__":
    main()
