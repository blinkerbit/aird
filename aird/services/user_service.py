"""User/account-oriented service facade."""

from __future__ import annotations

from typing import Any

from aird.repositories.db_repositories import UserRepository


class UserService:
    def __init__(self, repo: UserRepository):
        self.repo = repo

    def search_users(self, conn: Any, query: str) -> list[dict[str, Any]]:
        return self.repo.search_users(conn, query)

    def get_user(self, conn: Any, username: str) -> dict[str, Any] | None:
        return self.repo.get_by_username(conn, username)

    def list_users(self, conn: Any) -> list[dict[str, Any]]:
        return self.repo.list_all(conn)

    def get_user_quota(self, conn: Any, username: str) -> dict[str, Any]:
        return self.repo.get_quota(conn, username)

    def authenticate(
        self, conn: Any, username: str, password: str
    ) -> dict[str, Any] | None:
        return self.repo.authenticate(conn, username, password)

    def create_user(
        self, conn: Any, username: str, password: str, role: str = "user"
    ) -> Any:
        return self.repo.create(conn, username, password, role=role)

    def update_user(self, conn: Any, user_id: int, **kwargs: Any) -> bool:
        return self.repo.update(conn, user_id, **kwargs)

    def delete_user(self, conn: Any, user_id: int) -> bool:
        return self.repo.delete(conn, user_id)

    def log_audit(
        self,
        conn: Any,
        action: str,
        *,
        username: str | None = None,
        details: str | None = None,
        ip: str | None = None,
    ) -> None:
        self.repo.audit(conn, action, username=username, details=details, ip=ip)
