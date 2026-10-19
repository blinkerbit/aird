"""Listen-port failures stay on the requested port unless retry is opted in."""

import pytest

from aird.main import _start_server, bind_ports


def test_bind_ports_stays_on_requested_port(monkeypatch):
    monkeypatch.delenv("AIRD_PORT_RETRY", raising=False)
    assert bind_ports(8000) == [8000]


def test_bind_ports_retries_when_enabled(monkeypatch):
    monkeypatch.setenv("AIRD_PORT_RETRY", "1")
    assert bind_ports(8000) == [8000, 8001, 8002]


def test_start_server_reports_bind_failure_without_advancing(monkeypatch):
    calls = []

    def bind(port, address=""):
        calls.append((port, address))
        exc = OSError("address already in use")
        exc.errno = 10048
        raise exc

    monkeypatch.delenv("AIRD_PORT_RETRY", raising=False)
    monkeypatch.setattr("aird.main.tornado.netutil.bind_sockets", bind)
    with pytest.raises(OSError):
        _start_server(None, 8000, "localhost", 1)
    assert calls == [(8000, "")]
