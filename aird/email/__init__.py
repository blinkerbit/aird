"""Outbound email (Brevo transactional API)."""

from aird.email.brevo import BrevoClient
from aird.email.resolve import resolve_user_email

__all__ = ["BrevoClient", "resolve_user_email"]
