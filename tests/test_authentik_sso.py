"""Authentik auto-login decision, admin save rules, and login wiring."""

import json
import sqlite3

import pytest
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import HTMLResponse

from aird.core.authentik_sso import (
    SECRET_HEADER,
    decide_authentik_login,
    load_authentik_sso,
    merge_authentik_sso,
    public_authentik_sso,
    save_authentik_sso,
)
from aird.db.schema import init_db
from aird.db.users import get_all_users, get_user_by_username
from aird.ms.ctx import AppState
from aird.ms.routers.admin import admin_authentik_sso
from aird.ms.routers.auth import login_get
from aird.services.audit_service import AuditService
from aird.services.user_service import UserService

_SECRET = "shared-authentik-secret"


def _config(**overrides):
    cfg = {
        "enabled": True,
        "username_header": "X-authentik-username",
        "secret": _SECRET,
    }
    cfg.update(overrides)
    return cfg


def _headers(username="alice", secret=_SECRET, header="X-authentik-username"):
    data = {SECRET_HEADER: secret}
    if username is not None:
        data[header] = username
    return data


def _active_user(**overrides):
    user = {
        "id": 7,
        "username": "alice",
        "role": "user",
        "active": True,
        "password_hash": "stored-password-hash",
        "must_change_password": False,
    }
    user.update(overrides)
    return user


def test_disabled_ignores_headers():
    def lookup(_username):
        raise AssertionError("lookup")

    decision = decide_authentik_login(_config(enabled=False), _headers(), lookup)
    assert decision.action == "form"
    assert decision.user is None


def test_missing_or_wrong_secret_shows_form_without_lookup(caplog):
    def lookup(_username):
        raise AssertionError("lookup")

    for headers in (
        _headers(secret=""),
        _headers(secret="nope"),
        {"X-authentik-username": "alice"},
    ):
        decision = decide_authentik_login(_config(), headers, lookup)
        assert decision.action == "form"
        assert decision.user is None
    assert _SECRET not in caplog.text
    assert "nope" not in caplog.text


def test_wrong_secret_uses_compare_digest(monkeypatch):
    seen = {}

    def _compare(left, right):
        seen["called"] = True
        return False

    monkeypatch.setattr("aird.core.authentik_sso.secrets.compare_digest", _compare)
    decision = decide_authentik_login(
        _config(), _headers(secret="other-value"), lambda _name: None
    )
    assert seen["called"] is True
    assert decision.action == "form"


def test_empty_username_with_valid_secret_shows_form():
    def lookup(_username):
        raise AssertionError("lookup")

    decision = decide_authentik_login(_config(), _headers(username="  "), lookup)
    assert decision.action == "form"


def test_unknown_user_is_forbidden_and_does_not_create():
    created = []

    def lookup(username):
        created.append(username)
        return None

    decision = decide_authentik_login(_config(), _headers(username="ghost"), lookup)
    assert decision.action == "forbidden"
    assert decision.user is None
    assert created == ["ghost"]


def test_inactive_user_is_forbidden():
    decision = decide_authentik_login(
        _config(),
        _headers(),
        lambda _name: _active_user(active=False),
    )
    assert decision.action == "forbidden"
    assert decision.user is None


def test_blank_secret_trusts_username_header():
    decision = decide_authentik_login(
        _config(secret=""),
        {"X-authentik-username": "alice"},
        lambda name: _active_user(username=name),
    )
    assert decision.action == "session"
    assert decision.user["username"] == "alice"

    missing = decide_authentik_login(
        _config(secret=""),
        {"X-authentik-username": "ghost"},
        lambda _name: None,
    )
    assert missing.action == "forbidden"

    inactive = decide_authentik_login(
        _config(secret=""),
        {"X-authentik-username": "alice"},
        lambda _name: _active_user(active=False),
    )
    assert inactive.action == "forbidden"
    assert inactive.user is None

    no_name = decide_authentik_login(_config(secret=""), {}, lambda _name: None)
    assert no_name.action == "form"


def test_active_user_signals_session_without_password_hash():
    decision = decide_authentik_login(
        _config(),
        _headers(),
        lambda name: _active_user(username=name, must_change_password=True),
    )
    assert decision.action == "session"
    assert decision.user["username"] == "alice"
    assert decision.user["must_change_password"] is True
    assert "password_hash" not in decision.user


def test_invalid_header_names_rejected_on_save():
    current = _config(enabled=False, secret="")
    for name in (
        "Cookie",
        "Authorization",
        "username",
        "X-bad header",
        "X-foo\nbar",
        SECRET_HEADER,
    ):
        with pytest.raises(ValueError, match="X-\\*"):
            merge_authentik_sso(
                current,
                {"username_header": name, "enabled": False, "secret": _SECRET},
            )


def test_enable_without_secret_allowed_and_blank_secret_kept():
    merged_blank = merge_authentik_sso(
        _config(enabled=False, secret=""),
        {
            "enabled": True,
            "secret": "   ",
            "username_header": "X-authentik-username",
        },
    )
    assert merged_blank["enabled"] is True
    assert merged_blank["secret"] == ""

    merged = merge_authentik_sso(
        _config(secret="kept-secret"),
        {"enabled": True, "secret": "", "username_header": "x-custom-user"},
    )
    assert merged["secret"] == "kept-secret"
    assert merged["username_header"] == "X-custom-user"
    view = public_authentik_sso(merged)
    assert view["secret_set"] is True
    assert "secret" not in view
    assert "kept-secret" not in json.dumps(view)


def test_rejected_header_does_not_replace_stored_secret():
    conn = sqlite3.connect(":memory:")
    init_db(conn)
    save_authentik_sso(
        conn,
        {
            "enabled": True,
            "username_header": "X-authentik-username",
            "secret": _SECRET,
        },
    )
    with pytest.raises(ValueError):
        save_authentik_sso(
            conn,
            {
                "enabled": True,
                "username_header": "Cookie",
                "secret": "replacement-secret",
            },
        )
    stored = load_authentik_sso(conn)
    assert stored["secret"] == _SECRET
    assert stored["username_header"] == "X-authentik-username"


class _Ctx:
    def __init__(self, conn, services):
        self.db_conn = conn
        self.services = services
        self.event_bus = None

    def get_service(self, name, default=None):
        return self.services.get(name, default)


def _insert_user(conn, username, *, active=1, must_change=0, role="user"):
    conn.execute(
        """
        INSERT INTO users
            (username, password_hash, role, created_at, active, must_change_password)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (username, "stored-password-hash", role, "2020-01-01T00:00:00", active, must_change),
    )
    conn.commit()


def _request(conn, headers, *, query=b"next=/files/", method="GET", body=b"", admin=False):
    services = {"user_service": UserService(), "audit_service": AuditService()}
    ctx = _Ctx(conn, services)
    settings = {
        "cookie_secret": "test-cookie-secret",
        "db_conn": conn,
        "services": services,
        "app_context": ctx,
    }
    app = Starlette()
    app.state.aird = AppState(settings=settings, db_conn=conn, services=services)
    pairs = headers.items() if isinstance(headers, dict) else headers
    raw_headers = [
        (str(name).lower().encode("latin-1"), str(value).encode("latin-1"))
        for name, value in pairs
    ]
    if admin:
        raw_headers.extend(
            [
                (b"x-aird-user", b"admin"),
                (b"x-aird-role", b"admin"),
                (b"x-xsrftoken", b"tok"),
                (b"cookie", b"_xsrf=tok"),
            ]
        )
    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": method,
        "scheme": "http",
        "path": "/login" if method == "GET" else "/admin/api/authentik-sso",
        "raw_path": b"/login",
        "query_string": query,
        "headers": raw_headers,
        "client": ("127.0.0.1", 1234),
        "server": ("testserver", 80),
        "app": app,
    }

    async def receive():
        return {"type": "http.request", "body": body, "more_body": False}

    return Request(scope, receive)


def _session_count(conn):
    return conn.execute("SELECT COUNT(*) FROM user_sessions").fetchone()[0]


def _cookie_names(response):
    return [item.split("=", 1)[0] for item in response.headers.getlist("set-cookie")]


@pytest.fixture
def conn():
    db = sqlite3.connect(":memory:")
    init_db(db)
    yield db
    db.close()


@pytest.mark.asyncio
async def test_login_get_disabled_renders_form(conn, monkeypatch):
    monkeypatch.setattr(
        "aird.ms.routers.auth.render_html",
        lambda request, template, **kwargs: HTMLResponse("login-form"),
    )
    response = await login_get(
        _request(
            conn,
            {
                SECRET_HEADER: _SECRET,
                "X-authentik-username": "alice",
            },
        )
    )
    assert response.status_code == 200
    assert response.body == b"login-form"
    assert _cookie_names(response) == []
    assert _session_count(conn) == 0
    assert get_all_users(conn) == []


@pytest.mark.asyncio
async def test_login_get_bad_secret_renders_form(conn, monkeypatch):
    save_authentik_sso(
        conn,
        {"enabled": True, "username_header": "X-authentik-username", "secret": _SECRET},
    )
    monkeypatch.setattr(
        "aird.ms.routers.auth.render_html",
        lambda request, template, **kwargs: HTMLResponse("login-form"),
    )
    response = await login_get(
        _request(conn, {SECRET_HEADER: "wrong", "X-authentik-username": "alice"})
    )
    assert response.status_code == 200
    assert response.body == b"login-form"
    assert _session_count(conn) == 0


@pytest.mark.asyncio
async def test_login_get_unknown_user_forbidden_without_creating(conn):
    save_authentik_sso(
        conn,
        {"enabled": True, "username_header": "X-authentik-username", "secret": _SECRET},
    )
    response = await login_get(
        _request(conn, {SECRET_HEADER: _SECRET, "X-authentik-username": "ghost"})
    )
    body = response.body.decode()
    assert response.status_code == 403
    assert "Access is not allowed" in body
    assert "ghost" not in body
    assert _SECRET not in body
    assert _cookie_names(response) == []
    assert _session_count(conn) == 0
    assert get_all_users(conn) == []


@pytest.mark.asyncio
async def test_login_get_inactive_user_forbidden(conn):
    save_authentik_sso(
        conn,
        {"enabled": True, "username_header": "X-authentik-username", "secret": _SECRET},
    )
    _insert_user(conn, "alice", active=0)
    response = await login_get(
        _request(conn, {SECRET_HEADER: _SECRET, "X-authentik-username": "alice"})
    )
    assert response.status_code == 403
    assert _session_count(conn) == 0
    assert get_user_by_username(conn, "alice")["last_login"] in (None, "")


@pytest.mark.asyncio
async def test_login_get_active_user_sets_cookies_and_redirects(conn):
    save_authentik_sso(
        conn,
        {"enabled": True, "username_header": "X-authentik-username", "secret": _SECRET},
    )
    _insert_user(conn, "alice")
    response = await login_get(
        _request(conn, {SECRET_HEADER: _SECRET, "X-authentik-username": "alice"})
    )
    assert response.status_code == 302
    assert response.headers["location"] == "/files/"
    names = _cookie_names(response)
    assert "user" in names
    assert "user_role" in names
    assert "aird_session" in names
    assert _session_count(conn) == 1
    user = get_user_by_username(conn, "alice")
    assert user["last_login"]
    assert _SECRET not in " ".join(response.headers.getlist("set-cookie"))
    audit = conn.execute("SELECT action, username FROM audit_log").fetchone()
    assert audit[0] == "login"
    assert audit[1] == "alice"


@pytest.mark.asyncio
async def test_login_get_without_stored_secret_uses_username_header(conn):
    save_authentik_sso(
        conn,
        {"enabled": True, "username_header": "X-authentik-username", "secret": ""},
    )
    _insert_user(conn, "alice")
    response = await login_get(_request(conn, {"X-authentik-username": "alice"}))
    assert response.status_code == 302
    assert "user" in _cookie_names(response)
    assert _session_count(conn) == 1


@pytest.mark.asyncio
async def test_login_get_must_change_password_redirects(conn):
    save_authentik_sso(
        conn,
        {"enabled": True, "username_header": "X-authentik-username", "secret": _SECRET},
    )
    _insert_user(conn, "alice", must_change=1)
    response = await login_get(
        _request(
            conn,
            {SECRET_HEADER: _SECRET, "X-authentik-username": "alice"},
            query=b"next=https://evil.example/phish",
        )
    )
    assert response.status_code == 302
    assert response.headers["location"] == "/auth/mandatory-password?next=/files/"
    assert "user" in _cookie_names(response)


@pytest.mark.asyncio
async def test_admin_save_requires_admin_and_xsrf(conn):
    body = json.dumps(
        {"enabled": True, "username_header": "X-authentik-username", "secret": _SECRET}
    ).encode()
    denied = await admin_authentik_sso(
        _request(conn, [], method="POST", body=body, admin=False)
    )
    assert denied.status_code == 403
    assert load_authentik_sso(conn)["enabled"] is False

    no_xsrf = await admin_authentik_sso(
        _request(
            conn,
            [("x-aird-user", "admin"), ("x-aird-role", "admin")],
            method="POST",
            body=body,
            admin=False,
        )
    )
    assert no_xsrf.status_code == 403
    assert _SECRET not in no_xsrf.body.decode()
    assert load_authentik_sso(conn)["secret"] == ""


@pytest.mark.asyncio
async def test_admin_save_rejects_bad_header_and_hides_secret(conn):
    body = json.dumps(
        {"enabled": True, "username_header": "Cookie", "secret": _SECRET}
    ).encode()
    response = await admin_authentik_sso(
        _request(conn, [("content-type", "application/json")], method="POST", body=body, admin=True)
    )
    payload = json.loads(response.body)
    assert response.status_code == 400
    assert payload["ok"] is False
    assert _SECRET not in response.body.decode()
    assert "secret" not in payload

    ok_body = json.dumps(
        {
            "enabled": True,
            "username_header": "X-authentik-username",
            "secret": _SECRET,
        }
    ).encode()
    saved = await admin_authentik_sso(
        _request(conn, [("content-type", "application/json")], method="POST", body=ok_body, admin=True)
    )
    view = json.loads(saved.body)
    assert saved.status_code == 200
    assert view["ok"] is True
    assert view["secret_set"] is True
    assert "secret" not in view
    assert _SECRET not in saved.body.decode()


class _TornadoLogin:
    """Stand-in so LoginHandler.get can run the Authentik helper."""

    def __init__(self, conn, headers):
        self.db_conn = conn
        self.settings = {}
        self.current_user = None
        self.request = type(
            "Req",
            (),
            {"headers": headers, "remote_ip": "127.0.0.1"},
        )()
        self.services = {"user_service": UserService(), "audit_service": AuditService()}
        self.status = 200
        self.finished = None
        self.redirect_url = None
        self.rendered = None
        self.cookies: list[str] = []

    def get_service(self, name, default=None):
        return self.services.get(name, default)

    def _get_safe_next_url(self):
        return "/files/"

    def set_status(self, code):
        self.status = code

    def set_header(self, _name, _value):
        return None

    def finish(self, html):
        self.finished = html

    def redirect(self, url):
        self.redirect_url = url

    def render(self, template, **_kwargs):
        self.rendered = template

    def regenerate_session(self):
        return None

    def session_cookie_opts(self, expires_days=1):
        return {}

    def set_secure_cookie(self, name, _value, **_opts):
        self.cookies.append(name)

    def publish_event(self, _event):
        return None


def test_tornado_login_get_blank_secret_sets_session(conn):
    from aird.handlers.auth_handlers import LoginHandler

    save_authentik_sso(
        conn,
        {"enabled": True, "username_header": "X-authentik-username", "secret": ""},
    )
    _insert_user(conn, "alice", must_change=1)
    handler = _TornadoLogin(conn, {"X-authentik-username": "alice"})
    LoginHandler.get(handler)
    assert handler.redirect_url == "/auth/mandatory-password?next=%2Ffiles%2F"
    assert "user" in handler.cookies
    assert "aird_session" in handler.cookies
    assert handler.rendered is None
    assert _session_count(conn) == 1
    assert get_user_by_username(conn, "alice")["last_login"]


def test_tornado_login_get_unknown_user_forbidden(conn):
    from aird.handlers.auth_handlers import LoginHandler

    save_authentik_sso(
        conn,
        {"enabled": True, "username_header": "X-authentik-username", "secret": _SECRET},
    )
    handler = _TornadoLogin(
        conn, {SECRET_HEADER: _SECRET, "X-authentik-username": "ghost"}
    )
    LoginHandler.get(handler)
    assert handler.status == 403
    assert "Access is not allowed" in handler.finished
    assert handler.rendered is None
    assert handler.cookies == []
    assert get_all_users(conn) == []


def test_tornado_login_get_disabled_renders_form(conn):
    from aird.handlers.auth_handlers import LoginHandler

    handler = _TornadoLogin(
        conn, {SECRET_HEADER: _SECRET, "X-authentik-username": "alice"}
    )
    LoginHandler.get(handler)
    assert handler.rendered == "login.html"
    assert handler.redirect_url is None
    assert handler.cookies == []
    assert _session_count(conn) == 0
