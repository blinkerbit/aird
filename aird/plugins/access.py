"""Admin mapping of experimental plugins to none / all / named users."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone

from aird.plugins.chat import chat_library_available
from aird.plugins.gitlab import gitlab_library_available

SCOPE_NONE = "none"
SCOPE_ALL = "all"
SCOPE_USERS = "users"
SCOPES = frozenset({SCOPE_NONE, SCOPE_ALL, SCOPE_USERS})

PLUGIN_CHAT = "direct_messages"
PLUGIN_GITLAB = "gitlab"
PLUGIN_ONEDRIVE = "onedrive"
PLUGIN_ONEDRIVE_BROWSER = "onedrive_browser"

# Missing row: keep existing plugins usable when their flag is on.
_DEFAULT_SCOPE = {
    PLUGIN_CHAT: SCOPE_ALL,
    PLUGIN_GITLAB: SCOPE_ALL,
    PLUGIN_ONEDRIVE: SCOPE_NONE,
    PLUGIN_ONEDRIVE_BROWSER: SCOPE_ALL,
}


# audience: "user" = end-user UI (nav / profile); "admin" = admin console apps.
def catalog() -> list[dict]:
    return [
        {
            "id": PLUGIN_CHAT,
            "label": "Direct messages",
            "flag": "direct_messages",
            "available": chat_library_available(),
            "audience": "user",
            "href": "/chat",
        },
        {
            "id": PLUGIN_GITLAB,
            "label": "GitLab",
            "flag": "gitlab_integration",
            "available": gitlab_library_available(),
            "audience": "admin",
            "href": "/gitlab",
        },
        {
            "id": PLUGIN_ONEDRIVE,
            "label": "OneDrive host backup",
            "flag": "onedrive_backup",
            "available": True,
            "audience": "admin",
            "href": "/admin/plugins?p=onedrive",
        },
        {
            "id": PLUGIN_ONEDRIVE_BROWSER,
            "label": "OneDrive browser (SSO)",
            "flag": "onedrive_browser",
            "available": True,
            "audience": "user",
            "href": "/files/",
        },
    ]


def user_facing_plugins(
    username: str | None,
    conn: sqlite3.Connection | None,
) -> list[dict]:
    """Plugins meant for end users that are enabled and assigned to this account."""
    from aird.utils.util import is_feature_enabled

    out: list[dict] = []
    for item in catalog():
        if item.get("audience") != "user":
            continue
        if not item.get("available"):
            continue
        if not is_feature_enabled(item["flag"], False):
            continue
        if not user_may_use_plugin(item["id"], username, conn):
            continue
        out.append(item)
    return out


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _normalize_names(names: list[str] | None) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for raw in names or []:
        name = str(raw or "").strip()
        if not name or name in seen:
            continue
        seen.add(name)
        out.append(name)
    return out


def get_assignment(conn: sqlite3.Connection | None, plugin_id: str) -> dict:
    default = {
        "plugin_id": plugin_id,
        "scope": _DEFAULT_SCOPE.get(plugin_id, SCOPE_NONE),
        "usernames": [],
    }
    if conn is None:
        return default
    row = conn.execute(
        "SELECT plugin_id, scope, usernames_json FROM plugin_access WHERE plugin_id = ?",
        (plugin_id,),
    ).fetchone()
    if not row:
        return default
    try:
        names = json.loads(row[2] or "[]")
    except json.JSONDecodeError:
        names = []
    if not isinstance(names, list):
        names = []
    scope = row[1] if row[1] in SCOPES else default["scope"]
    return {"plugin_id": row[0], "scope": scope, "usernames": _normalize_names(names)}


def set_assignment(
    conn: sqlite3.Connection,
    plugin_id: str,
    scope: str,
    usernames: list[str] | None = None,
) -> dict:
    if scope not in SCOPES:
        raise ValueError("Invalid plugin access scope")
    names = _normalize_names(usernames)
    if scope != SCOPE_USERS:
        names = []
    conn.execute(
        """
        INSERT INTO plugin_access (plugin_id, scope, usernames_json, updated_at)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(plugin_id) DO UPDATE SET
            scope = excluded.scope,
            usernames_json = excluded.usernames_json,
            updated_at = excluded.updated_at
        """,
        (plugin_id, scope, json.dumps(names), _now()),
    )
    conn.commit()
    return get_assignment(conn, plugin_id)


def list_assignments(conn: sqlite3.Connection | None) -> dict[str, dict]:
    return {item["id"]: get_assignment(conn, item["id"]) for item in catalog()}


def user_may_use_plugin(
    plugin_id: str,
    username: str | None,
    conn: sqlite3.Connection | None,
) -> bool:
    assignment = get_assignment(conn, plugin_id)
    scope = assignment["scope"]
    if scope == SCOPE_NONE:
        return False
    if scope == SCOPE_ALL:
        return True
    name = (username or "").strip()
    if not name:
        return False
    return name in assignment["usernames"]


def bind_template_plugin_checks(username: str | None, conn: sqlite3.Connection | None) -> dict:
    from aird.plugins.chat import is_chat_enabled
    from aird.plugins.gitlab import is_gitlab_enabled
    from aird.plugins.onedrive import is_onedrive_enabled
    from aird.plugins.onedrive_browser import is_onedrive_browser_enabled

    return {
        "is_chat_enabled": lambda: is_chat_enabled()
        and user_may_use_plugin(PLUGIN_CHAT, username, conn),
        "is_gitlab_enabled": lambda: is_gitlab_enabled()
        and user_may_use_plugin(PLUGIN_GITLAB, username, conn),
        "is_onedrive_enabled": lambda: is_onedrive_enabled()
        and user_may_use_plugin(PLUGIN_ONEDRIVE, username, conn),
        "is_onedrive_browser_enabled": lambda: is_onedrive_browser_enabled()
        and user_may_use_plugin(PLUGIN_ONEDRIVE_BROWSER, username, conn),
        "onedrive_browser_public_config": lambda: _onedrive_browser_public_config(conn),
    }


def _onedrive_browser_public_config(conn: sqlite3.Connection | None) -> dict:
    from aird.plugins.onedrive_browser.settings import public_config

    return public_config(conn)
