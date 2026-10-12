"""Administration microservice (Granian/Starlette, port 8007)."""

from aird.ms.routers.admin import routes as admin_routes
from aird.ms.routers.extra_api import routes as extra_routes
from aird.ms.routers.health import routes as health_routes
from aird.ms.routers.ws_extra import routes as ws_extra_routes
from aird.ms.service_runtime import create_service_app, select_routes, serve

SERVICE = "aird-admin"

routes = [
    *health_routes,
    *admin_routes,
    *select_routes(extra_routes, prefixes=("/admin/",)),
    *select_routes(ws_extra_routes, exact=("/ws/policy-decisions",)),
]
from aird.ms.routers.ldap_admin import routes as ldap_routes

routes.extend(ldap_routes)

app = create_service_app(SERVICE, routes)


def main() -> None:
    serve(__name__, SERVICE, "AIRD_ADMIN_HOST", "AIRD_ADMIN_PORT", 8007)


if __name__ == "__main__":
    main()
