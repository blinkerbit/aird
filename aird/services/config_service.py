"""Configuration orchestration service."""

from __future__ import annotations

import logging
from typing import Any

import aird.constants as constants
from aird.db.config import (
    load_allowed_extensions,
    load_feature_flags,
    load_server_config,
    load_upload_config,
    save_allowed_extensions,
    save_feature_flags,
    save_server_config,
    save_upload_config,
    save_websocket_config,
)

logger = logging.getLogger(__name__)


class ConfigService:
    def sync_upload_config_from_db(self, conn: Any) -> None:
        """Reload upload limits from SQLite (required with multiple workers)."""
        if conn is None:
            return
        persisted_upload = load_upload_config(conn)
        constants.merge_persisted_upload_config(persisted_upload)
        if persisted_upload:
            for key, value in persisted_upload.items():
                logger.debug(
                    "Upload config '%s' set to %s from database", key, int(value)
                )

    def merge_from_db(self, conn: Any) -> None:
        persisted_flags = load_feature_flags(conn)
        if persisted_flags:
            for key, value in persisted_flags.items():
                constants.FEATURE_FLAGS[key] = bool(value)
                logger.debug(
                    "Feature flag '%s' set to %s from database", key, bool(value)
                )

        self.sync_upload_config_from_db(conn)
        self.sync_runtime_config_from_db(conn)

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

    def sync_runtime_config_from_db(self, conn: Any) -> dict:
        """Refresh revision from SQLite and return the browser-safe strategy."""
        if conn is not None:
            persisted = load_server_config(conn)
            try:
                revision = int(
                    persisted.get("revision", constants.TRANSFER_CONFIG_REVISION)
                )
            except (TypeError, ValueError):
                revision = 0
            constants.TRANSFER_CONFIG_REVISION = max(0, revision)
            constants.refresh_upload_derived_constants()
        return constants.get_effective_transfer_strategy()

    def get_runtime_config(self, conn: Any) -> dict:
        """Return the latest effective transfer strategy from shared SQLite."""
        if conn is not None:
            self.sync_upload_config_from_db(conn)
        return self.sync_runtime_config_from_db(conn)

    def bump_runtime_config_revision(self, conn: Any) -> dict:
        """Persist a bumped revision and apply it locally."""
        revision = save_server_config(conn, {}, bump_revision=True)
        constants.TRANSFER_CONFIG_REVISION = revision
        constants.refresh_upload_derived_constants()
        return constants.get_effective_transfer_strategy()

    def load_allowed_extensions(self, conn: Any) -> set[str]:
        return load_allowed_extensions(conn)

    def save_allowed_extensions(self, conn: Any, extensions: set[str]) -> None:
        save_allowed_extensions(conn, extensions)
