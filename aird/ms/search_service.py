"""Search microservice (Granian/Starlette, port 8004)."""

from aird.ms.routers.browse import routes as browse_routes
from aird.ms.routers.health import routes as health_routes
from aird.ms.routers.ws import routes as ws_routes
from aird.ms.service_runtime import create_service_app, select_routes, serve

SERVICE = "aird-search"

routes = [
    *health_routes,
    *select_routes(browse_routes, exact=("/search",)),
    *select_routes(ws_routes, exact=("/search/ws",)),
]

app = create_service_app(SERVICE, routes)


def main() -> None:
    serve(__name__, SERVICE, "AIRD_SEARCH_HOST", "AIRD_SEARCH_PORT", 8004)


if __name__ == "__main__":
    main()
