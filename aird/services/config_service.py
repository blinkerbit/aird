"""Configuration orchestration service."""

from __future__ import annotations

import logging
from typing import Any

import aird.constants as constants
from aird.db.config import (
    load_allowed_extensions,
    load_feature_flags,
    load_upload_config,
    save_allowed_extensions,
    save_feature_flags,
    save_upload_config,
    save_websocket_config,
)

logger = logging.getLogger(__name__)


class ConfigService:
    def merge_from_db(self, conn: Any) -> None:
        persisted_flags = load_feature_flags(conn)
        if persisted_flags:
            for key, value in persisted_flags.items():
                constants.FEATURE_FLAGS[key] = bool(value)
                logger.debug(
                    "Feature flag '%s' set to %s from database", key, bool(value)
                )

        persisted_upload = load_upload_config(conn)
        if persisted_upload:
            for key, value in persisted_upload.items():
                constants.UPLOAD_CONFIG[key] = int(value)
                logger.debug(
                    "Upload config '%s' set to %s from database", key, int(value)
                )
        constants.MAX_FILE_SIZE = (
            constants.UPLOAD_CONFIG["max_file_size_mb"] * 1024 * 1024
        )

        constants.UPLOAD_ALLOWED_EXTENSIONS = load_allowed_extensions(conn)
        if not constants.UPLOAD_ALLOWED_EXTENSIONS:
            constants.UPLOAD_ALLOWED_EXTENSIONS = set(
                constants.ALLOWED_UPLOAD_EXTENSIONS
            )
            save_allowed_extensions(conn, constants.UPLOAD_ALLOWED_EXTENSIONS)
            logger.info("Seeded upload allowed extensions from defaults")

    def load_feature_flags(self, conn: Any) -> dict[str, Any]:
        return load_feature_flags(conn)

    def save_feature_flags(self, conn: Any, flags: dict[str, Any]) -> None:
        save_feature_flags(conn, flags)

    def save_websocket_config(self, conn: Any, ws_config: dict) -> None:
        save_websocket_config(conn, ws_config)

    def save_upload_config(self, conn: Any, upload_config: dict) -> None:
        save_upload_config(conn, upload_config)

    def load_allowed_extensions(self, conn: Any) -> set[str]:
        return load_allowed_extensions(conn)

    def save_allowed_extensions(self, conn: Any, extensions: set[str]) -> None:
        save_allowed_extensions(conn, extensions)
