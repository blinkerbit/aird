"""User/account-oriented service facade."""

from __future__ import annotations

from typing import Any

from aird.db.users import (
    authenticate_user,
    assign_admin_privileges,
    create_user,
    delete_user,
    get_all_users,
    get_user_by_username,
    search_users,
    update_user,
)
from aird.db.quota import get_user_quota


class UserService:
    def search_users(self, conn: Any, query: str) -> list[dict[str, Any]]:
        return search_users(conn, query)

    def get_user(self, conn: Any, username: str) -> dict[str, Any] | None:
        return get_user_by_username(conn, username)

    def list_users(self, conn: Any) -> list[dict[str, Any]]:
        return get_all_users(conn)

    def get_user_quota(self, conn: Any, username: str) -> dict[str, Any]:
        return get_user_quota(conn, username)

    def authenticate(
        self, conn: Any, username: str, password: str
    ) -> dict[str, Any] | None:
        return authenticate_user(conn, username, password)

    def create_user(
        self, conn: Any, username: str, password: str, role: str = "user"
    ) -> Any:
        return create_user(conn, username, password, role=role)

    def update_user(self, conn: Any, user_id: int, **kwargs: Any) -> bool:
        return update_user(conn, user_id, **kwargs)

    def delete_user(self, conn: Any, user_id: int) -> bool:
        return delete_user(conn, user_id)

    def assign_admin_privileges(self, conn: Any, admin_users: list[str]) -> None:
        assign_admin_privileges(conn, admin_users)
