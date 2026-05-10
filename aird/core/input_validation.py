"""Validate and bound user-supplied strings (server-side)."""

from __future__ import annotations

import json
from typing import Any

from aird.constants.input_limits import (
    GLOB_PATTERN_MAX_LEN,
    InputTooLongError,
    LOGIN_PASSWORD_MAX_LEN,
    LOGIN_USERNAME_MAX_LEN,
    MAX_SHARE_GLOB_LINE_LEN,
    MAX_SHARE_GLOB_LINES,
    MAX_SHARE_PATHS,
    MAX_SHARE_PATH_STRING_LEN,
    MAX_SHARE_USERNAMES,
    POLICY_CONDITION_JSON_MAX_CHARS,
    POLICY_DESCRIPTION_MAX_LEN,
    POLICY_NAME_MAX_LEN,
    RESOURCE_TAG_MAX_LEN,
    SHARE_ID_MAX_LEN,
    SHARE_TAG_NAME_MAX_LEN,
    SHARE_USERNAME_ENTRY_MAX_LEN,
    WS_SEARCH_PATTERN_MAX_LEN,
    WS_SEARCH_TEXT_MAX_LEN,
)


def require_max_chars(value: Any, *, max_len: int, field: str = "field") -> str:
    """Return stripped str or raise InputTooLongError."""
    if value is None:
        return ""
    s = str(value)
    if len(s) > max_len:
        raise InputTooLongError(f"{field} exceeds maximum length ({max_len})")
    return s


def bound_username_for_login(handler: Any) -> str:
    """Username argument for login forms (LDAP-safe upper bound)."""
    raw = handler.get_argument("username", "")
    if len(raw) > LOGIN_USERNAME_MAX_LEN:
        raise InputTooLongError("username too long")
    return raw.strip()


def bound_login_password(handler: Any) -> str:
    raw = handler.get_argument("password", "")
    if len(raw) > LOGIN_PASSWORD_MAX_LEN:
        raise InputTooLongError("password too long")
    return raw


def bound_access_token(handler: Any) -> str:
    from aird.constants.input_limits import ACCESS_TOKEN_MAX_LEN

    raw = handler.get_argument("token", "").strip()
    if len(raw) > ACCESS_TOKEN_MAX_LEN:
        raise InputTooLongError("token too long")
    return raw


def validate_ws_search(pattern: Any, search_text: Any) -> tuple[str, str]:
    p = "" if pattern is None else str(pattern)
    t = "" if search_text is None else str(search_text)
    if len(p) > WS_SEARCH_PATTERN_MAX_LEN or len(t) > WS_SEARCH_TEXT_MAX_LEN:
        raise InputTooLongError("search parameters too long")
    return p, t


def validate_super_search_glob(pattern: str) -> str | None:
    """Reject glob patterns that look like path traversal or absolute OS paths.

    Matching only ever runs against paths **relative to the user's file root**; this
    blocks confusing or malicious pattern text. Return an error message, or None if ok.
    """
    if "\x00" in pattern:
        return "pattern contains invalid characters"
    norm = pattern.replace("\\", "/").strip()
    if not norm:
        return "pattern is empty"
    if norm.startswith("//"):
        return "pattern must not be an absolute or UNC-style path"
    if len(norm) >= 2 and norm[1] == ":":
        return "patterns with a drive letter are not allowed"
    for segment in norm.split("/"):
        if segment == "..":
            return "pattern must not contain '..' path segments"
    return None


def validate_abac_tag_rule(tag: str, glob_pattern: str) -> None:
    if len(tag) > RESOURCE_TAG_MAX_LEN or len(glob_pattern) > GLOB_PATTERN_MAX_LEN:
        raise InputTooLongError("tag or glob_pattern too long")


def validate_policy_payload(
    name: str,
    description: str,
    target_actions: list[str],
    condition: dict[str, Any],
) -> None:
    if len(name) > POLICY_NAME_MAX_LEN:
        raise InputTooLongError("name too long")
    if len(description) > POLICY_DESCRIPTION_MAX_LEN:
        raise InputTooLongError("description too long")
    from aird.constants.input_limits import MAX_POLICY_TARGET_ACTIONS

    if len(target_actions) > MAX_POLICY_TARGET_ACTIONS:
        raise InputTooLongError("too many target_actions")
    for a in target_actions:
        if len(str(a)) > 120:
            raise InputTooLongError("action name too long")
    cond_str = json.dumps(condition, ensure_ascii=False)
    if len(cond_str) > POLICY_CONDITION_JSON_MAX_CHARS:
        raise InputTooLongError("condition JSON too large")


def validate_share_create_struct(data: dict[str, Any]) -> str | None:
    """Return error message or None if sizes are acceptable."""
    st = str(data.get("share_type", "static") or "static")
    if st == "tag":
        tn = str(data.get("tag_name") or "").strip()
        if len(tn) > SHARE_TAG_NAME_MAX_LEN:
            return "tag_name too long"
        return None

    paths = data.get("paths") or []
    if not isinstance(paths, list):
        return "paths must be a list"
    if len(paths) > MAX_SHARE_PATHS:
        return "too many paths"
    for p in paths:
        if isinstance(p, str):
            ps = p.strip().strip("/")
            if len(ps) > MAX_SHARE_PATH_STRING_LEN:
                return "path too long"
        elif isinstance(p, dict):
            continue
        else:
            return "paths entries must be strings or objects"

    globs_users_err = _validate_share_user_and_globs(data)
    if globs_users_err:
        return globs_users_err
    tag_n = data.get("tag_name")
    if tag_n and len(str(tag_n).strip()) > SHARE_TAG_NAME_MAX_LEN:
        return "tag_name too long"
    return None


def validate_share_update_struct(data: dict[str, Any]) -> str | None:
    """Structural limits for PATCH-style share payloads."""
    sid = data.get("share_id")
    if sid is not None and len(str(sid)) > SHARE_ID_MAX_LEN:
        return "share_id too long"

    paths = data.get("paths")
    if paths is not None:
        if not isinstance(paths, list):
            return "paths must be a list"
        if len(paths) > MAX_SHARE_PATHS:
            return "too many paths"
        for p in paths:
            if isinstance(p, str):
                ps = p.strip().strip("/")
                if len(ps) > MAX_SHARE_PATH_STRING_LEN:
                    return "path too long"
            elif isinstance(p, dict):
                continue
            else:
                return "paths entries must be strings or objects"

    rf = data.get("remove_files")
    if rf is not None:
        if not isinstance(rf, list):
            return "remove_files must be a list"
        if len(rf) > MAX_SHARE_PATHS:
            return "too many remove_files entries"
        for p in rf:
            if isinstance(p, str) and len(p.strip()) > MAX_SHARE_PATH_STRING_LEN:
                return "remove_files path too long"

    return _validate_share_user_and_globs(data)


def _validate_share_user_and_globs(data: dict[str, Any]) -> str | None:
    """Validate allowed_users / modify_users / allow_list / avoid_list fragments."""
    for key in ("allowed_users", "modify_users"):
        items = data.get(key) or []
        if items is None:
            continue
        if not isinstance(items, list):
            return f"{key} must be a list"
        if len(items) > MAX_SHARE_USERNAMES:
            return f"too many {key}"
        for u in items:
            if not isinstance(u, str):
                return f"{key} entries must be strings"
            if len(u.strip()) > SHARE_USERNAME_ENTRY_MAX_LEN:
                return f"{key} entry too long"

    for key in ("allow_list", "avoid_list"):
        items = data.get(key) or []
        if isinstance(items, list):
            if len(items) > MAX_SHARE_GLOB_LINES:
                return f"too many {key} patterns"
            for line in items:
                if isinstance(line, str) and len(line) > MAX_SHARE_GLOB_LINE_LEN:
                    return f"{key} pattern too long"
    return None
def validate_user_attribute(username: str, key: str, value: str) -> None:
    from aird.constants.input_limits import LOGIN_USERNAME_MAX_LEN as UMAX
    from aird.constants.input_limits import USER_ATTR_KEY_MAX_LEN
    from aird.constants.input_limits import USER_ATTR_VALUE_MAX_LEN

    if len(username) > UMAX or len(key) > USER_ATTR_KEY_MAX_LEN:
        raise InputTooLongError("username or key too long")
    if len(value) > USER_ATTR_VALUE_MAX_LEN:
        raise InputTooLongError("value too long")
