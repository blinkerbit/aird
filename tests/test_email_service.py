"""Tests for Brevo email notifications."""

from unittest.mock import MagicMock, patch

import pytest

from aird.email.brevo import BrevoClient
from aird.email.resolve import looks_like_email, resolve_user_email
from aird.services.email_service import EmailService, public_base_url


class TestResolveUserEmail:
    def test_username_as_email(self):
        assert resolve_user_email(None, "alice@example.com") == "alice@example.com"

    def test_user_attribute_email(self):
        conn = MagicMock()
        with patch(
            "aird.email.resolve.get_user_attributes",
            return_value={"email": "bob@example.com"},
        ):
            assert resolve_user_email(conn, "bob") == "bob@example.com"

    def test_no_email(self):
        conn = MagicMock()
        with patch("aird.email.resolve.get_user_attributes", return_value={}):
            assert resolve_user_email(conn, "bob") is None


def test_looks_like_email():
    assert looks_like_email("a@b.co")
    assert not looks_like_email("not-an-email")


@patch("aird.email.brevo.requests.post")
def test_brevo_send(mock_post):
    mock_post.return_value = MagicMock(status_code=201, text="{}")
    client = BrevoClient("key", sender_email="noreply@aird.test", sender_name="Aird")
    assert client.send(
        "user@example.com",
        "Hello",
        html_content="<p>Hi</p>",
        text_content="Hi",
    )
    mock_post.assert_called_once()
    body = mock_post.call_args.kwargs["json"]
    assert body["to"][0]["email"] == "user@example.com"


@patch("aird.services.email_service.is_feature_enabled", return_value=True)
@patch.object(BrevoClient, "send", return_value=True)
def test_notify_share_created(mock_send, _mock_flag):
    conn = MagicMock()
    svc = EmailService(
        BrevoClient("key", sender_email="noreply@aird.test"),
    )
    with patch(
        "aird.services.email_service.resolve_user_email",
        side_effect=lambda _c, u: f"{u}@example.com",
    ), patch("aird.services.email_service.public_base_url", return_value="https://aird.test"):
        sent = svc.notify_share_created(
            conn,
            share_id="abc123",
            creator="owner",
            recipient_usernames=["alice", "owner"],
            path_count=3,
        )
    assert sent == 1
    mock_send.assert_called_once()


@patch("aird.config.SSL_CERT", "/cert.pem")
@patch("aird.config.HOSTNAME", "files.example.com")
@patch("aird.config.PORT", 443)
def test_public_base_url_https_default_port():
    assert public_base_url() == "https://files.example.com"
