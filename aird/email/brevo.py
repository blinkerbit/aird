"""Brevo (Sendinblue) transactional email client."""

from __future__ import annotations

import logging
from typing import Any

import requests

logger = logging.getLogger(__name__)

BREVO_API_URL = "https://api.brevo.com/v3/smtp/email"


class BrevoError(RuntimeError):
    pass


class BrevoClient:
    def __init__(
        self,
        api_key: str | None,
        *,
        sender_email: str | None,
        sender_name: str = "Aird",
    ):
        self.api_key = (api_key or "").strip()
        self.sender_email = (sender_email or "").strip()
        self.sender_name = (sender_name or "Aird").strip() or "Aird"

    @property
    def configured(self) -> bool:
        return bool(self.api_key and self.sender_email)

    def send(
        self,
        to_email: str,
        subject: str,
        *,
        html_content: str,
        text_content: str | None = None,
        to_name: str | None = None,
    ) -> bool:
        if not self.configured:
            logger.debug("Brevo not configured; skipping email to %s", to_email)
            return False
        to_email = to_email.strip()
        if not to_email:
            return False

        payload: dict[str, Any] = {
            "sender": {"email": self.sender_email, "name": self.sender_name},
            "to": [{"email": to_email, "name": (to_name or to_email).strip()}],
            "subject": subject,
            "htmlContent": html_content,
        }
        if text_content:
            payload["textContent"] = text_content

        try:
            resp = requests.post(
                BREVO_API_URL,
                headers={
                    "api-key": self.api_key,
                    "Content-Type": "application/json",
                    "accept": "application/json",
                },
                json=payload,
                timeout=30,
            )
        except requests.RequestException as exc:
            logger.warning("Brevo request failed for %s: %s", to_email, exc)
            return False

        if resp.status_code >= 400:
            logger.warning(
                "Brevo API error %s for %s: %s",
                resp.status_code,
                to_email,
                resp.text[:500],
            )
            return False
        return True
