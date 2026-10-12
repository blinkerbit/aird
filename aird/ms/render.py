"""HTML rendering via Tornado template loader (syntax-compatible with aird/templates)."""

from __future__ import annotations

from pathlib import Path

from starlette.requests import Request
from starlette.responses import HTMLResponse
from tornado.template import Loader

from aird.ms.ctx import (
    csp_nonce,
    ensure_xsrf_cookie,
    get_state,
    security_headers,
    template_globals,
)

_loader: Loader | None = None


def _get_loader(template_path: str) -> Loader:
    global _loader
    if _loader is None or getattr(_loader, "root", None) != template_path:
        _loader = Loader(template_path)
    return _loader


def render_html(request: Request, template_name: str, **kwargs) -> HTMLResponse:
    state = get_state(request)
    nonce = csp_nonce()
    ns = template_globals(request, nonce, **kwargs)
    html = _get_loader(state.template_path).load(template_name).generate(**ns)
    if isinstance(html, bytes):
        body = html
    else:
        body = str(html).encode("utf-8")
    response = HTMLResponse(body)
    for k, v in security_headers(nonce, https=request.url.scheme == "https").items():
        response.headers[k] = v
    ensure_xsrf_cookie(request, response)
    return response


def templates_dir() -> str:
    return str(Path(__file__).resolve().parent.parent / "templates")


def static_dir() -> str:
    return str(Path(__file__).resolve().parent.parent / "static")
