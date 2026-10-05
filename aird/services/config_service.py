"""Configuration orchestration service."""

from __future__ import annotations

import logging
from typing import Any

import aird.constants as constants
from aird.db.config import (
    load_allowed_extensions,
    load_feature_flags,
    load_json_server_config,
    load_server_config,
    load_upload_config,
    load_websocket_config,
    save_allowed_extensions,
    save_feature_flags,
    save_json_server_config,
    save_server_config,
    save_upload_config,
    save_websocket_config,
    seed_runtime_defaults,
)

logger = logging.getLogger(__name__)

_feature_flag_broadcasters: list = []


def register_feature_flag_broadcaster(callback) -> None:
    if callback not in _feature_flag_broadcasters:
        _feature_flag_broadcasters.append(callback)


def _notify_feature_flag_listeners() -> None:
    for callback in tuple(_feature_flag_broadcasters):
        try:
            callback()
        except Exception:
            logger.debug("feature flag live broadcast failed", exc_info=True)


class ConfigService:
    def seed_defaults(self, conn: Any) -> None:
        """Insert missing runtime keys only; existing SQLite values win."""
        if conn is None:
            return
        seed_runtime_defaults(
            conn,
            feature_flags=constants.FEATURE_FLAGS,
            websocket_config=constants.WEBSOCKET_CONFIG,
            upload_config=constants.UPLOAD_CONFIG,
            transfer_config=constants.TRANSFER_CONFIG,
            compression_config=constants.COMPRESSION_CONFIG,
            hosting_profile=getattr(constants, "TRANSFER_PROFILE", "open"),
        )

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

    def sync_websocket_config_from_db(self, conn: Any) -> None:
        if conn is None:
            return
        persisted = load_websocket_config(conn)
        if persisted:
            constants.WEBSOCKET_CONFIG.update({k: int(v) for k, v in persisted.items()})

    def sync_transfer_limits_from_db(self, conn: Any) -> None:
        data = load_json_server_config(conn, "transfer_config") if conn is not None else {}
        if data:
            constants.TRANSFER_CONFIG.update(data)

    def sync_compression_from_db(self, conn: Any) -> None:
        data = (
            load_json_server_config(conn, "compression_config") if conn is not None else {}
        )
        if data:
            constants.COMPRESSION_CONFIG.update(data)

    def save_transfer_limits(self, conn: Any, cfg: dict) -> None:
        save_json_server_config(conn, "transfer_config", cfg)

    def save_compression_config(self, conn: Any, cfg: dict) -> None:
        save_json_server_config(conn, "compression_config", cfg)

    def merge_from_db(self, conn: Any) -> None:
        if conn is None:
            return
        self.seed_defaults(conn)
        persisted_flags = load_feature_flags(conn)
        if persisted_flags:
            for key, value in persisted_flags.items():
                constants.FEATURE_FLAGS[key] = bool(value)
                logger.debug(
                    "Feature flag '%s' set to %s from database", key, bool(value)
                )

        self.sync_upload_config_from_db(conn)
        self.sync_websocket_config_from_db(conn)
        self.sync_transfer_profile_from_db(conn)
        self.sync_transfer_limits_from_db(conn)
        self.sync_compression_from_db(conn)

        from aird.core.rate_limit import TransferRateLimiter

        TransferRateLimiter.apply_transfer_config(constants.TRANSFER_CONFIG)

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

    def apply_feature_flag(self, conn: Any, key: str, enabled: bool) -> dict[str, Any]:
        """Persist one feature flag. Unknown keys return status 400."""
        from aird.network_share_manager import (
            smb_library_available,
            webdav_library_available,
        )
        from aird.plugins.chat import chat_library_available
        from aird.plugins.gitlab import gitlab_library_available
        from aird.utils.util import invalidate_feature_flags_cache

        if key not in constants.FEATURE_FLAGS:
            return {"ok": False, "error": "Unknown feature flag", "status": 400}

        value = bool(enabled)
        notice = ""
        if value:
            if key == "direct_messages" and not chat_library_available():
                value = False
                notice = "chat_package_required"
            elif key == "gitlab_integration" and not gitlab_library_available():
                value = False
                notice = "gitlab_package_required"
            elif key == "webdav_server" and not webdav_library_available():
                value = False
                notice = "webdav_package_required"
            elif key == "smb_server" and not smb_library_available():
                value = False
                notice = "smb_package_required"

        constants.FEATURE_FLAGS[key] = value
        if conn is not None:
            self.save_feature_flags(conn, constants.FEATURE_FLAGS)
            persisted = self.load_feature_flags(conn)
            if persisted:
                constants.FEATURE_FLAGS.update(persisted)
        invalidate_feature_flags_cache()
        _notify_feature_flag_listeners()
        return {
            "ok": True,
            "flag": key,
            "enabled": bool(constants.FEATURE_FLAGS.get(key)),
            "notice": notice,
        }
