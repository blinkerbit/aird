"""Serve the OpenAPI document and Swagger UI."""

import json

import tornado.web


class OpenAPISpecHandler(tornado.web.RequestHandler):
    """GET /api/openapi.json"""

    def get(self):
        from aird.openapi_spec import openapi_document

        self.set_header("Content-Type", "application/json; charset=utf-8")
        self.set_header("Cache-Control", "no-store")
        self.write(json.dumps(openapi_document()))


class OpenAPIDocsHandler(tornado.web.RequestHandler):
    """GET /api/docs — Swagger UI for the same document."""

    def get(self):
        self.set_header("Content-Type", "text/html; charset=utf-8")
        self.set_header("Cache-Control", "no-store")
        self.set_header("X-Content-Type-Options", "nosniff")
        self.set_header(
            "Content-Security-Policy",
            "default-src 'self'; "
            "script-src 'self'; "
            "style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data:; "
            "font-src 'self' data:; "
            "connect-src 'self'; "
            "frame-ancestors 'none'",
        )
        self.render("api_docs.html")
