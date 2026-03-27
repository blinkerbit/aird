"""Thin repository adapters over existing aird.db functions.

These classes intentionally delegate to current database helpers to keep
behavior stable while establishing clear architectural boundaries.
"""

from __future__ import annotations

from typing import Any

from aird.db import (
    authenticate_user,
    assign_admin_privileges,
    cleanup_expired_shares,
    create_user,
    delete_user,
    get_all_network_shares,
    get_all_shares,
    get_all_users,
    get_share_by_id,
    get_shares_for_path,
    get_user_by_username,
    get_user_quota,
    load_allowed_extensions,
    load_feature_flags,
    load_upload_config,
    log_audit,
    save_allowed_extensions,
    search_users,
    update_user,
)


class ConfigRepository:
    def load_feature_flags(self, conn: Any) -> dict[str, Any]:
        return load_feature_flags(conn)

    def load_upload_config(self, conn: Any) -> dict[str, Any]:
        return load_upload_config(conn)

    def load_allowed_extensions(self, conn: Any) -> set[str]:
        return load_allowed_extensions(conn)

    def save_allowed_extensions(self, conn: Any, extensions: set[str]) -> None:
        save_allowed_extensions(conn, extensions)


class NetworkShareRepository:
    def list_all(self, conn: Any) -> list[dict[str, Any]]:
        return get_all_network_shares(conn)


class ShareRepository:
    def list_all(self, conn: Any) -> dict[str, Any]:
        return get_all_shares(conn)

    def get_by_id(self, conn: Any, share_id: str) -> dict[str, Any] | None:
        return get_share_by_id(conn, share_id)

    def list_for_path(self, conn: Any, path: str) -> list[dict[str, Any]]:
        return get_shares_for_path(conn, path)

    def cleanup_expired(self, conn: Any) -> int:
        return cleanup_expired_shares(conn)


class UserRepository:
    def assign_admin_privileges(self, conn: Any, admin_users: list[str]) -> None:
        assign_admin_privileges(conn, admin_users)

    def search_users(self, conn: Any, query: str) -> list[dict[str, Any]]:
        return search_users(conn, query)

    def get_by_username(self, conn: Any, username: str) -> dict[str, Any] | None:
        return get_user_by_username(conn, username)

    def list_all(self, conn: Any) -> list[dict[str, Any]]:
        return get_all_users(conn)

    def get_quota(self, conn: Any, username: str) -> dict[str, Any]:
        return get_user_quota(conn, username)

    def authenticate(
        self, conn: Any, username: str, password: str
    ) -> dict[str, Any] | None:
        return authenticate_user(conn, username, password)

    def create(
        self, conn: Any, username: str, password: str, role: str = "user"
    ) -> Any:
        return create_user(conn, username, password, role=role)

    def update(self, conn: Any, user_id: int, **kwargs: Any) -> bool:
        return update_user(conn, user_id, **kwargs)

    def delete(self, conn: Any, user_id: int) -> bool:
        return delete_user(conn, user_id)

    def audit(
        self,
        conn: Any,
        action: str,
        *,
        username: str | None = None,
        details: str | None = None,
        ip: str | None = None,
    ) -> None:
        log_audit(conn, action, username=username, details=details, ip=ip)
