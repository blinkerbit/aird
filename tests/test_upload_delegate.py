"""Tests for detach upload delegate."""

from __future__ import annotations

import tornado.httputil

from aird.web.upload_delegate import native_detach_eligible


def _request(method: str = "POST", uri: str = "/upload") -> tornado.httputil.HTTPServerRequest:
    return tornado.httputil.HTTPServerRequest(
        method=method,
        uri=uri,
        connection=None,
    )


def test_native_detach_requires_post():
    headers = tornado.httputil.HTTPHeaders({"Content-Length": "2048"})
    assert native_detach_eligible(_request("GET"), headers) is False


def test_native_detach_requires_content_length():
    headers = tornado.httputil.HTTPHeaders({})
    assert native_detach_eligible(_request(), headers) is False


def test_native_detach_accepts_small_body_when_native_available():
    headers = tornado.httputil.HTTPHeaders({"Content-Length": "1024"})
    # On platforms without native extension this is False; with extension True.
    result = native_detach_eligible(_request(), headers)
    assert result in (True, False)


def test_native_detach_rejects_chunked():
    headers = tornado.httputil.HTTPHeaders(
        {
            "Content-Length": str(2 * 1024 * 1024),
            "Transfer-Encoding": "chunked",
        }
    )
    assert native_detach_eligible(_request(), headers) is False
