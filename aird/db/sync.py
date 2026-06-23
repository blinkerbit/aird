"""Process-wide SQLite serialization for free-threaded (nogil) Python."""

from __future__ import annotations

import sqlite3
import threading
from contextlib import contextmanager
from typing import Any, Iterator

DB_LOCK = threading.RLock()


@contextmanager
def db_sync() -> Iterator[None]:
    """Serialize access to the shared SQLite connection."""
    with DB_LOCK:
        yield


class _LockedCursor:
    __slots__ = ("_cursor",)

    def __init__(self, cursor: sqlite3.Cursor) -> None:
        self._cursor = cursor

    def __getattr__(self, name: str) -> Any:
        attr = getattr(self._cursor, name)
        if not callable(attr):
            return attr

        def locked(*args: Any, **kwargs: Any) -> Any:
            with db_sync():
                return attr(*args, **kwargs)

        return locked

    def __iter__(self) -> Iterator[Any]:
        with db_sync():
            for row in self._cursor:
                yield row


class ThreadSafeConnection:
    """Wrap a sqlite3.Connection so every operation holds DB_LOCK."""

    __slots__ = ("_conn",)

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def __getattr__(self, name: str) -> Any:
        attr = getattr(self._conn, name)
        if not callable(attr):
            return attr

        def locked(*args: Any, **kwargs: Any) -> Any:
            with db_sync():
                return attr(*args, **kwargs)

        return locked

    def cursor(self, *args: Any, **kwargs: Any) -> _LockedCursor:
        with db_sync():
            return _LockedCursor(self._conn.cursor(*args, **kwargs))

    def execute(self, *args: Any, **kwargs: Any) -> sqlite3.Cursor:
        with db_sync():
            return self._conn.execute(*args, **kwargs)

    def executemany(self, *args: Any, **kwargs: Any) -> sqlite3.Cursor:
        with db_sync():
            return self._conn.executemany(*args, **kwargs)

    def executescript(self, *args: Any, **kwargs: Any) -> sqlite3.Cursor:
        with db_sync():
            return self._conn.executescript(*args, **kwargs)

    def commit(self) -> None:
        with db_sync():
            self._conn.commit()

    def rollback(self) -> None:
        with db_sync():
            self._conn.rollback()

    def close(self) -> None:
        with db_sync():
            self._conn.close()

    def __enter__(self) -> ThreadSafeConnection:
        with db_sync():
            self._conn.__enter__()
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> Any:
        with db_sync():
            return self._conn.__exit__(exc_type, exc_val, exc_tb)


def wrap_connection(conn: sqlite3.Connection) -> ThreadSafeConnection:
    """Return a thread-safe wrapper around *conn*."""
    return ThreadSafeConnection(conn)
