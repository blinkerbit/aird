"""Core browse/file-operation microservice (Granian/Starlette, port 8001)."""

from aird.ms.routers.browse import routes as browse_routes
from aird.ms.routers.config_api import routes as config_routes
from aird.ms.routers.extra_api import routes as extra_routes
from aird.ms.routers.file_ops import routes as file_ops_routes
from aird.ms.routers.health import routes as health_routes
from aird.ms.routers.ws import routes as ws_routes
from aird.ms.routers.ws_extra import routes as ws_extra_routes
from aird.ms.service_runtime import (
    create_service_app,
    exclude_routes,
    select_routes,
    serve,
)

SERVICE = "aird-browse"

routes = [
    *health_routes,
    *exclude_routes(browse_routes, exact=("/search", "/p2p")),
    *file_ops_routes,
    *config_routes,
    *exclude_routes(
        extra_routes,
        prefixes=("/api/webauthn", "/share/", "/admin/"),
    ),
    *exclude_routes(ws_routes, exact=("/search/ws",)),
    *select_routes(ws_extra_routes, exact=("/ws/bulk",)),
]

app = create_service_app(SERVICE, routes, mount_static=True)


def main() -> None:
    serve(__name__, SERVICE, "AIRD_BROWSE_HOST", "AIRD_BROWSE_PORT", 8001)


if __name__ == "__main__":
    main()
