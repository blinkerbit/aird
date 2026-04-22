"""Health check endpoint for load balancers and monitoring."""

import logging

import tornado.web
import aird.constants as constants_module

logger = logging.getLogger(__name__)


class HealthHandler(tornado.web.RequestHandler):
    """GET /health returns JSON status. Returns 503 when a required
    dependency (e.g. the configured SQLite DB) is unreachable so that
    load balancers / orchestrators can react correctly."""

    def get(self):
        self.set_header("Content-Type", "application/json")
        self.set_header("Cache-Control", "no-store")
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
            self.set_status(503)

        self.write(status)
