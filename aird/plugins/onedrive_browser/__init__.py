"""Frontend-only OneDrive SSO. Tokens never leave the browser."""

from __future__ import annotations


def is_onedrive_browser_enabled() -> bool:
    from aird.utils.util import is_feature_enabled

    return is_feature_enabled("onedrive_browser", False)


def register_onedrive_browser(routes: list) -> None:
    from aird.plugins.onedrive_browser.handlers import (
        OneDriveBrowserCallbackHandler,
        OneDriveBrowserConfigHandler,
    )

    routes.extend(
        [
            (r"/onedrive-browser/callback", OneDriveBrowserCallbackHandler),
            (r"/api/onedrive-browser/config", OneDriveBrowserConfigHandler),
        ]
    )
