"""Health check endpoint for load balancers and monitoring."""

import tornado.web
import aird.constants as constants_module


class HealthHandler(tornado.web.RequestHandler):
    """GET /health returns 200 with optional JSON status."""

    def get(self):
        self.set_header("Content-Type", "application/json")
        status = {"status": "ok"}
        # Optional checks
        if constants_module.DB_CONN is not None:
            try:
                constants_module.DB_CONN.execute("SELECT 1").fetchone()
                status["db"] = "ok"
            except Exception:
                status["db"] = "error"
        else:
            status["db"] = "not_configured"
        self.write(status)
