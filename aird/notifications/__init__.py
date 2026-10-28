"""Realtime alerts: open socket first, Web Push when that device does not confirm."""

from aird.notifications.engine import deliver, start_retry_loop

__all__ = ["deliver", "start_retry_loop"]
