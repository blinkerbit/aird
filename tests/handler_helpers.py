"""
Shared helpers for Tornado handler unit tests.
"""

from contextlib import contextmanager
from unittest.mock import MagicMock, patch


def _default_services():
    """Return real service instances so handler.get_service() never returns None."""
    from aird.services import (
        AuditService,
        ConfigService,
        FavoritesService,
        NetworkShareService,
        QuotaService,
        ShareService,
        UserService,
    )

    return {
        "audit_service": AuditService(),
        "config_service": ConfigService(),
        "favorites_service": FavoritesService(),
        "network_share_service": NetworkShareService(),
        "quota_service": QuotaService(),
        "share_service": ShareService(),
        "user_service": UserService(),
    }


def _ensure_mock(attr_name, handler):
    attr = getattr(handler, attr_name, None)
    if not isinstance(attr, MagicMock):
        setattr(handler, attr_name, MagicMock())


def prepare_handler(handler):
    """Ensure minimal Tornado handler internals exist for isolated testing."""
    if not getattr(handler, "_transforms", None):
        handler._transforms = []
    handler._write_buffer = getattr(handler, "_write_buffer", [])
    handler._headers_written = getattr(handler, "_headers_written", False)
    if handler.request is None:
        handler.request = MagicMock()
    if not getattr(handler.request, "connection", None):
        handler.request.connection = MagicMock()
    if not getattr(handler.request.connection, "context", None):
        handler.request.connection.context = MagicMock()
    if not getattr(handler.request, "headers", None):
        handler.request.headers = {}
    if not getattr(handler.request, "path", None):
        handler.request.path = "/test"
    if not getattr(handler.request, "protocol", None):
        handler.request.protocol = "http"

    # Default mocks for common methods so tests can assert calls safely
    for method_name in (
        "write",
        "set_status",
        "render",
        "redirect",
        "finish",
        "flush",
        "clear_cookie",
        "set_header",
    ):
        _ensure_mock(method_name, handler)

    if not getattr(handler, "get_argument", None):
        handler.get_argument = MagicMock()
    if not getattr(handler, "get_secure_cookie", None):
        handler.get_secure_cookie = MagicMock(return_value=None)
    if not getattr(handler, "set_secure_cookie", None):
        handler.set_secure_cookie = MagicMock()

    settings = getattr(handler.application, "settings", None)
    if isinstance(settings, dict) and "services" not in settings:
        settings["services"] = _default_services()

    return handler


def authenticate(handler, role="admin", username=None):
    """Stub get_current_user to satisfy @authenticated decorators."""
    prepare_handler(handler)
    username = username or ("admin" if role == "admin" else "user")
    user = {"username": username, "role": role}
    handler._current_user = user
    handler.get_current_user = MagicMock(return_value=user)
    return user


@contextmanager
def patch_db_conn(value, modules=None):
    """
    Patch DB_CONN in aird.constants and BaseHandler property.
    We patch BaseHandler.db_conn using PropertyMock so all handlers
    reading self.db_conn get the mocked connection.
    Extra module paths can be provided via `modules` for legacy compatibility.
    """
    from unittest.mock import PropertyMock

    targets = [
        "aird.constants.DB_CONN",
        "aird.handlers.base_handler.constants_module.DB_CONN",
    ]

    modules = modules or []
    for module in modules:
        targets.append(f"{module}.constants_module.DB_CONN")
        targets.append(f"{module}.DB_CONN")

    patches = []
    active_patches = []
    try:
        for target in targets:
            patches.append(patch(target, value, create=True))

        # Patch the BaseHandler property correctly
        prop_patch = patch(
            "aird.handlers.base_handler.BaseHandler.db_conn",
            new_callable=PropertyMock,
            return_value=value,
        )
        patches.append(prop_patch)

        for p in patches:
            try:
                p.start()
            except (AttributeError, ModuleNotFoundError):
                continue
            else:
                active_patches.append(p)
        yield value
    finally:
        for p in reversed(active_patches):
            p.stop()
