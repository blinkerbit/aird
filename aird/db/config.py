"""Configuration database operations (feature flags, upload, websocket, extensions)."""

from __future__ import annotations

import json
import logging
import sqlite3

logger = logging.getLogger(__name__)

_TRANSFER_CONFIG_KEY = "transfer_config"
_COMPRESSION_CONFIG_KEY = "compression_config"


_INT_SEED_SQL = {
    "feature_flags": "INSERT OR IGNORE INTO feature_flags (key, value) VALUES (?, ?)",
    "websocket_config": "INSERT OR IGNORE INTO websocket_config (key, value) VALUES (?, ?)",
    "upload_config": "INSERT OR IGNORE INTO upload_config (key, value) VALUES (?, ?)",
}


def _seed_int_rows(conn: sqlite3.Connection, table: str, defaults: dict) -> None:
    sql = _INT_SEED_SQL.get(table)
    if not sql:
        raise ValueError(f"Unknown config table {table}")
    with conn:
        for key, value in defaults.items():
            conn.execute(sql, (str(key), int(value)))


def seed_runtime_defaults(
    conn: sqlite3.Connection,
    *,
    feature_flags: dict,
    websocket_config: dict,
    upload_config: dict,
    transfer_config: dict | None = None,
    compression_config: dict | None = None,
    hosting_profile: str = "open",
) -> None:
    """Write missing keys only so existing DB values stay the source of truth."""
    try:
        _seed_int_rows(
            conn,
            "feature_flags",
            {k: (1 if v else 0) for k, v in feature_flags.items()},
        )
        _seed_int_rows(conn, "websocket_config", websocket_config)
        _seed_int_rows(conn, "upload_config", upload_config)
        existing = load_server_config(conn)
        to_write: dict[str, object] = {}
        if "hosting_profile" not in existing:
            to_write["hosting_profile"] = hosting_profile
        if transfer_config is not None and _TRANSFER_CONFIG_KEY not in existing:
            to_write[_TRANSFER_CONFIG_KEY] = json.dumps(transfer_config)
        if compression_config is not None and _COMPRESSION_CONFIG_KEY not in existing:
            to_write[_COMPRESSION_CONFIG_KEY] = json.dumps(compression_config)
        if to_write:
            save_server_config(conn, to_write, bump_revision=False)
    except Exception:
        logger.warning("seed_runtime_defaults failed", exc_info=True)


def load_json_server_config(conn: sqlite3.Connection, key: str) -> dict:
    raw = load_server_config(conn).get(key)
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _as_bool(value) -> bool:
    if isinstance(value, str):
        return value.strip().lower() not in {"0", "false", "off", "no", ""}
    return bool(value)


def load_feature_flags(conn: sqlite3.Connection) -> dict:
    try:
        rows = conn.execute("SELECT key, value FROM feature_flags").fetchall()
        return {k: _as_bool(v) for (k, v) in rows}
    except Exception:
        return {}


def save_feature_flags(conn: sqlite3.Connection, flags: dict) -> None:
    try:
        with conn:
            for k, v in flags.items():
                conn.execute(
                    "REPLACE INTO feature_flags (key, value) VALUES (?, ?)",
                    (k, 1 if v else 0),
                )
    except Exception:
        logger.warning("save_feature_flags failed", exc_info=True)


def load_upload_config(conn: sqlite3.Connection) -> dict:
    """Load upload configuration from SQLite database."""
    try:
        rows = conn.execute("SELECT key, value FROM upload_config").fetchall()
        return {k: int(v) for (k, v) in rows}
    except Exception:
        return {}


def save_upload_config(conn: sqlite3.Connection, config: dict) -> None:
    """Save upload configuration to SQLite database."""
    try:
        with conn:
            for key, value in config.items():
                conn.execute(
                    "INSERT OR REPLACE INTO upload_config (key, value) VALUES (?, ?)",
                    (key, int(value)),
                )
    except Exception:
        logger.warning("save_upload_config failed", exc_info=True)


def load_server_config(conn: sqlite3.Connection) -> dict[str, str]:
    """Load string-valued runtime server configuration."""
    try:
        rows = conn.execute("SELECT key, value FROM server_config").fetchall()
        return {str(key): str(value) for key, value in rows}
    except Exception:
        return {}


def save_server_config(
    conn: sqlite3.Connection,
    config: dict[str, object],
    *,
    bump_revision: bool = False,
) -> int:
    """Persist runtime config and return its monotonic revision."""
    try:
        with conn:
            for key, value in config.items():
                conn.execute(
                    "INSERT OR REPLACE INTO server_config (key, value) VALUES (?, ?)",
                    (str(key), str(value)),
                )
            row = conn.execute(
                "SELECT value FROM server_config WHERE key = 'revision'"
            ).fetchone()
            revision = int(row[0]) if row else 0
            if bump_revision:
                revision += 1
                conn.execute(
                    "INSERT OR REPLACE INTO server_config (key, value) "
                    "VALUES ('revision', ?)",
                    (str(revision),),
                )
            return revision
    except Exception:
        logger.warning("save_server_config failed", exc_info=True)
        return 0


def load_allowed_extensions(conn: sqlite3.Connection) -> set:
    """Load allowed upload extensions from database."""
    try:
        rows = conn.execute("SELECT ext FROM upload_allowed_extensions").fetchall()
        return {row[0] for row in rows}
    except Exception:
        return set()


def save_allowed_extensions(conn: sqlite3.Connection, extensions: set) -> None:
    """Replace stored allowed extensions with the given set."""
    try:
        with conn:
            conn.execute("DELETE FROM upload_allowed_extensions")
            for ext in extensions:
                if ext and isinstance(ext, str) and ext.startswith("."):
                    conn.execute(
                        "INSERT INTO upload_allowed_extensions (ext) VALUES (?)", (ext,)
                    )
    except Exception:
        logger.debug("save_allowed_extensions failed", exc_info=True)


def load_websocket_config(conn: sqlite3.Connection) -> dict:
    """Load WebSocket configuration from SQLite database."""
    try:
        rows = conn.execute("SELECT key, value FROM websocket_config").fetchall()
        return {k: int(v) for (k, v) in rows}
    except Exception:
        return {}


def save_websocket_config(conn: sqlite3.Connection, config: dict) -> None:
    """Save WebSocket configuration to SQLite database."""
    try:
        with conn:
            for key, value in config.items():
                conn.execute(
                    "INSERT OR REPLACE INTO websocket_config (key, value) VALUES (?, ?)",
                    (key, int(value)),
                )
    except Exception:
        logger.debug("save_websocket_config failed", exc_info=True)


def save_json_server_config(conn: sqlite3.Connection, key: str, payload: dict) -> None:
    if not isinstance(payload, dict):
        return
    try:
        encoded = json.dumps(payload)
    except (TypeError, ValueError):
        logger.warning("save_json_server_config skipped unserializable %s", key)
        return
    save_server_config(conn, {key: encoded}, bump_revision=False)
