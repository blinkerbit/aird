"""CSP connect-src extras for browser plugins that call third-party HTTPS APIs."""

from __future__ import annotations


def connect_src() -> str:
    wide = False
    try:
        from aird.plugins.gitlab import is_gitlab_enabled

        wide = wide or is_gitlab_enabled()
    except Exception:
        pass
    try:
        from aird.plugins.onedrive_browser import is_onedrive_browser_enabled

        wide = wide or is_onedrive_browser_enabled()
    except Exception:
        pass
    livekit = ""
    try:
        from aird.plugins.calls import is_calls_enabled
        from aird.plugins.calls.config import livekit_ws_origin

        if is_calls_enabled():
            origin = livekit_ws_origin()
            if origin:
                livekit = f" {origin}"
    except Exception:
        pass
    if wide:
        return f"'self' https: http://127.0.0.1:* http://localhost:*{livekit}"
    if livekit:
        return f"'self'{livekit}"
    return "'self'"
