"""Tag service: maps file paths to ABAC resource tags via glob patterns.

The same fnmatch-based engine used by share allow/avoid lists is reused so
operators can write patterns like ``finance/*.pdf`` or ``*.log`` without
learning a new syntax.
"""

from __future__ import annotations

import threading
import time
from typing import Any

from aird.core.file_operations import matches_glob_patterns
from aird.db.resource_tags import (
    delete_resource_tag,
    insert_resource_tag,
    list_resource_tags,
)

_DEFAULT_TTL = 5.0  # seconds; matches feature-flag cache pattern


class TagService:
    """Cached glob-based tag resolver."""

    def __init__(self, *, cache_ttl: float = _DEFAULT_TTL):
        self._cache_ttl = cache_ttl
        self._cache: list[dict] | None = None
        self._cache_loaded_at: float = 0.0
        self._lock = threading.Lock()

    # --- CRUD --------------------------------------------------------------

    def apply(
        self,
        conn: Any,
        tag: str,
        glob_pattern: str,
        *,
        priority: int = 0,
        created_by: str | None = None,
    ) -> int | None:
        result = insert_resource_tag(
            conn,
            tag,
            glob_pattern,
            priority=priority,
            created_by=created_by,
        )
        if result is not None:
            self.invalidate()
        return result

    def remove(self, conn: Any, tag_id: int) -> bool:
        ok = delete_resource_tag(conn, tag_id)
        if ok:
            self.invalidate()
        return ok

    def list(self, conn: Any) -> list[dict]:
        return list_resource_tags(conn)

    # --- Resolution --------------------------------------------------------

    def invalidate(self) -> None:
        with self._lock:
            self._cache = None
            self._cache_loaded_at = 0.0

    def _load(self, conn: Any) -> list[dict]:
        with self._lock:
            now = time.time()
            if (
                self._cache is not None
                and (now - self._cache_loaded_at) < self._cache_ttl
            ):
                return self._cache
            self._cache = list_resource_tags(conn)
            self._cache_loaded_at = now
            return self._cache

    def resolve(self, conn: Any, path: str | None) -> tuple[str, ...]:
        """Return the unique set of tags whose glob matches *path*.

        Tags are ordered by descending priority then insertion order. Returns
        an empty tuple for falsy paths.
        """
        if not path:
            return ()
        rules = self._load(conn)
        if not rules:
            return ()
        # Normalise path separators for fnmatch consistency on Windows.
        normalised = path.replace("\\", "/")
        ordered: list[str] = []
        seen: set[str] = set()
        for rule in rules:
            pattern = rule.get("glob_pattern", "")
            if not pattern:
                continue
            if matches_glob_patterns(normalised, [pattern]):
                tag = rule.get("tag")
                if tag and tag not in seen:
                    seen.add(tag)
                    ordered.append(tag)
        return tuple(ordered)
