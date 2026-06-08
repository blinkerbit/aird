"""Email notifications via Brevo."""

from __future__ import annotations

import html
import logging
import sqlite3
from typing import Iterable

import aird.config as app_config
from aird.email.brevo import BrevoClient
from aird.email.resolve import resolve_user_email
from aird.utils.util import is_feature_enabled

logger = logging.getLogger(__name__)


def public_base_url() -> str:
    override = getattr(app_config, "PUBLIC_BASE_URL", None)
    if override:
        return str(override).rstrip("/")
    scheme = "https" if getattr(app_config, "SSL_CERT", None) else "http"
    host = getattr(app_config, "HOSTNAME", None) or "localhost"
    port = int(getattr(app_config, "PORT", 8000) or 8000)
    if (scheme == "https" and port == 443) or (scheme == "http" and port == 80):
        return f"{scheme}://{host}"
    return f"{scheme}://{host}:{port}"


class EmailService:
    def __init__(self, client: BrevoClient | None = None):
        if client is None:
            client = BrevoClient(
                getattr(app_config, "BREVO_API_KEY", None),
                sender_email=getattr(app_config, "BREVO_SENDER_EMAIL", None),
                sender_name=getattr(app_config, "BREVO_SENDER_NAME", "Aird"),
            )
        self._client = client

    @property
    def enabled(self) -> bool:
        return (
            is_feature_enabled("email_notifications", False)
            and self._client.configured
        )

    def notify_share_created(
        self,
        conn: sqlite3.Connection | None,
        *,
        share_id: str,
        creator: str,
        recipient_usernames: Iterable[str],
        path_count: int,
    ) -> int:
        if not self.enabled or not conn:
            return 0
        base = public_base_url()
        share_path = f"/shared/{share_id}"
        share_url = f"{base}{share_path}"
        creator_safe = html.escape(creator or "someone")
        sent = 0
        seen_emails: set[str] = set()

        for username in recipient_usernames:
            uname = (username or "").strip()
            if not uname or uname == creator:
                continue
            email = resolve_user_email(conn, uname)
            if not email or email in seen_emails:
                continue
            seen_emails.add(email)
            subject = f"{creator} shared files with you on Aird"
            text = (
                f"Hi {uname},\n\n"
                f"{creator} shared {path_count} item(s) with you on Aird.\n\n"
                f"Open the share: {share_url}\n\n"
                "If the share requires a token, ask the person who shared it with you."
            )
            html_body = (
                f"<p>Hi {html.escape(uname)},</p>"
                f"<p><strong>{creator_safe}</strong> shared "
                f"<strong>{path_count}</strong> item(s) with you on Aird.</p>"
                f'<p><a href="{html.escape(share_url)}">Open share</a></p>'
                "<p><small>If prompted for an access token, contact the person who "
                "shared this with you.</small></p>"
            )
            if self._client.send(
                email,
                subject,
                html_content=html_body,
                text_content=text,
                to_name=uname,
            ):
                sent += 1
                logger.info("Share notification email sent to %s (%s)", uname, email)
        return sent
