"""Configuration orchestration service."""

from __future__ import annotations

import logging
from typing import Any

import aird.constants as constants
from aird.repositories.db_repositories import ConfigRepository

logger = logging.getLogger(__name__)


class ConfigService:
    def __init__(self, repo: ConfigRepository):
        self.repo = repo

    def merge_from_db(self, conn: Any) -> None:
        persisted_flags = self.repo.load_feature_flags(conn)
        if persisted_flags:
            for key, value in persisted_flags.items():
                constants.FEATURE_FLAGS[key] = bool(value)
                logger.debug(
                    "Feature flag '%s' set to %s from database", key, bool(value)
                )

        persisted_upload = self.repo.load_upload_config(conn)
        if persisted_upload:
            for key, value in persisted_upload.items():
                constants.UPLOAD_CONFIG[key] = int(value)
                logger.debug(
                    "Upload config '%s' set to %s from database", key, int(value)
                )
        constants.MAX_FILE_SIZE = (
            constants.UPLOAD_CONFIG["max_file_size_mb"] * 1024 * 1024
        )

        constants.UPLOAD_ALLOWED_EXTENSIONS = self.repo.load_allowed_extensions(conn)
        if not constants.UPLOAD_ALLOWED_EXTENSIONS:
            constants.UPLOAD_ALLOWED_EXTENSIONS = set(
                constants.ALLOWED_UPLOAD_EXTENSIONS
            )
            self.repo.save_allowed_extensions(conn, constants.UPLOAD_ALLOWED_EXTENSIONS)
            logger.info("Seeded upload allowed extensions from defaults")
