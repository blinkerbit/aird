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
        self.sync_transfer_profile_from_db(conn)

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

    def sync_transfer_profile_from_db(self, conn: Any) -> dict:
        """Refresh the effective profile and return its browser-safe strategy."""
        persisted = load_server_config(conn) if conn is not None else {}
        profile = persisted.get(
            "hosting_profile",
            constants.TRANSFER_PROFILE if conn is None else "open",
        )
        try:
            revision = int(
                persisted.get("revision", constants.TRANSFER_CONFIG_REVISION)
            )
        except (TypeError, ValueError):
            revision = 0
        constants.set_transfer_profile(profile, revision)
        strategy = constants.get_effective_transfer_strategy()
        strategy["configuredProfile"] = constants.normalize_transfer_profile(profile)
        strategy["environmentOverride"] = bool(
            constants.transfer_profile_env_override()
        )
        return strategy

    def get_runtime_config(self, conn: Any) -> dict:
        """Return the latest effective transfer strategy from shared SQLite."""
        if conn is not None:
            self.sync_upload_config_from_db(conn)
        return self.sync_transfer_profile_from_db(conn)

    def save_transfer_profile(self, conn: Any, profile: str) -> dict:
        """Persist a validated profile, bump revision, and apply it locally."""
        normalized = constants.normalize_transfer_profile(profile)
        revision = save_server_config(
            conn,
            {"hosting_profile": normalized},
            bump_revision=True,
        )
        constants.set_transfer_profile(normalized, revision)
        return self.sync_transfer_profile_from_db(conn)

    def load_allowed_extensions(self, conn: Any) -> set[str]:
        return load_allowed_extensions(conn)

    def save_allowed_extensions(self, conn: Any, extensions: set[str]) -> None:
        save_allowed_extensions(conn, extensions)
