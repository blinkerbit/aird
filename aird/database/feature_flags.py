"""Feature flags management.

Re-exports canonical implementations from aird.db to avoid duplication.
"""

from aird.db import (
    load_feature_flags,
    save_feature_flags,
    load_websocket_config,
    save_websocket_config,
)
from aird.utils.util import is_feature_enabled

__all__ = [
    "load_feature_flags",
    "save_feature_flags",
    "load_websocket_config",
    "save_websocket_config",
    "is_feature_enabled",
]
