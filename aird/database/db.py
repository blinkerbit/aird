"""Legacy connection helpers. Schema lives in aird.db.schema."""

import os
import sys
import sqlite3

# Global database connection
_DB_CONN = None
_DB_PATH = None


def get_data_dir() -> str:
    """Return OS-appropriate data directory for storing the SQLite DB."""
    try:
        if os.name == "nt":  # Windows
            base = (
                os.environ.get("LOCALAPPDATA")
                or os.environ.get("APPDATA")
                or os.path.expanduser("~\\AppData\\Local")
            )
        elif sys.platform == "darwin":  # macOS
            base = os.path.expanduser("~/Library/Application Support")
        else:  # Linux and others
            base = os.environ.get("XDG_DATA_HOME") or os.path.expanduser(
                "~/.local/share"
            )
        data_dir = os.path.join(base, "aird")
        os.makedirs(data_dir, exist_ok=True)
        return data_dir
    except Exception:
        # Fallback to current directory
        return os.getcwd()


def init_db(conn: sqlite3.Connection) -> None:
    """Delegate to aird.db.schema. Kept so older imports keep working."""
    import warnings

    from aird.db.schema import init_db as schema_init_db

    warnings.warn(
        "aird.database.db.init_db is deprecated; use aird.db.schema.init_db instead",
        DeprecationWarning,
        stacklevel=2,
    )
    schema_init_db(conn)


def set_db_conn(conn: sqlite3.Connection, db_path: str = None):
    """Set the global database connection."""
    global _DB_CONN, _DB_PATH
    _DB_CONN = conn
    _DB_PATH = db_path


def get_db_conn() -> sqlite3.Connection:
    """Get the global database connection."""
    return _DB_CONN


def get_db_path() -> str:
    """Get the database file path."""
    return _DB_PATH
