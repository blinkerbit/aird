"""P2P signaling microservice (Granian/Starlette, port 8006)."""

from aird.ms.routers.browse import routes as browse_routes
from aird.ms.routers.health import routes as health_routes
from aird.ms.routers.ws_extra import routes as ws_extra_routes
from aird.ms.service_runtime import create_service_app, select_routes, serve

SERVICE = "aird-p2p"

routes = [
    *health_routes,
    *select_routes(browse_routes, exact=("/p2p",)),
    *select_routes(ws_extra_routes, exact=("/p2p/signal",)),
]

app = create_service_app(SERVICE, routes)


def main() -> None:
    serve(__name__, SERVICE, "AIRD_P2P_HOST", "AIRD_P2P_PORT", 8006)


if __name__ == "__main__":
    main()
