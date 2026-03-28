"""Audit logging service."""

from __future__ import annotations

from typing import Any

from aird.db.audit import get_audit_logs, log_audit


class AuditService:
    def log(
        self,
        conn: Any,
        action: str,
        *,
        username: str | None = None,
        details: str | None = None,
        ip: str | None = None,
    ) -> None:
        log_audit(conn, action, username=username, details=details, ip=ip)

    def get_logs(
        self,
        conn: Any,
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        return get_audit_logs(conn, limit=limit, offset=offset)
