"""Admin-only OneDrive host sync plugin."""

from __future__ import annotations


def is_onedrive_enabled() -> bool:
    from aird.utils.util import is_feature_enabled

    return is_feature_enabled("onedrive_backup", False)


def register_onedrive_host(routes: list) -> None:
    from aird.plugins.onedrive_host.handlers import register_routes

    register_routes(routes)
