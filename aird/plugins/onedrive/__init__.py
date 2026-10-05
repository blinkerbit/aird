"""Legacy OneDrive plugin — host sync moved to onedrive_host."""

from __future__ import annotations


def is_onedrive_enabled() -> bool:
    from aird.plugins.onedrive_host import is_onedrive_enabled as _host

    return _host()


def register_onedrive(routes: list) -> None:
    """Deprecated: routes registered via onedrive_host."""
