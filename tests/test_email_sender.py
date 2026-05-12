"""Unit tests for the email sender ABC + built-in backends."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from sre_free_mcp.config import EmailConfig
from sre_free_mcp.email_sender import (
    EmailMessage,
    EmailSender,
    NullEmailSender,
    SmtpEmailSender,
    default_sender,
)


def test_email_sender_is_abstract():
    """EmailSender ABC cannot be instantiated directly."""
    with pytest.raises(TypeError):
        EmailSender()  # type: ignore[abstract]


def test_email_message_is_frozen():
    msg = EmailMessage(to=["a@b.com"], subject="s", body_text="t")
    with pytest.raises(Exception):
        msg.subject = "changed"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# NullEmailSender
# ---------------------------------------------------------------------------


def test_null_sender_records_messages():
    sender = NullEmailSender()
    msg = EmailMessage(to=["a@b.com"], subject="hi", body_text="body")
    sender.send(msg)
    assert sender.sent == [msg]


def test_null_sender_send_does_not_raise_on_empty_to():
    """A NullEmailSender should never raise — that's the whole point."""
    sender = NullEmailSender()
    sender.send(EmailMessage(to=[], subject="x", body_text="y"))
    assert len(sender.sent) == 1


# ---------------------------------------------------------------------------
# SmtpEmailSender — patch smtplib so no real network call happens
# ---------------------------------------------------------------------------


def test_smtp_sender_calls_login_and_sendmail():
    sender = SmtpEmailSender(
        host="smtp.example.com",
        port=587,
        username="u",
        password="p",
        from_address="sre@example.com",
    )
    fake_conn = MagicMock()
    with patch("sre_free_mcp.email_sender.smtplib.SMTP") as fake_smtp:
        fake_smtp.return_value.__enter__.return_value = fake_conn
        sender.send(
            EmailMessage(
                to=["a@b.com", "c@d.com"],
                subject="alert",
                body_text="plain",
                body_html="<p>html</p>",
            )
        )
    fake_smtp.assert_called_once_with("smtp.example.com", 587)
    fake_conn.starttls.assert_called_once()
    fake_conn.login.assert_called_once_with("u", "p")
    fake_conn.sendmail.assert_called_once()
    args = fake_conn.sendmail.call_args.args
    assert args[0] == "sre@example.com"
    assert args[1] == ["a@b.com", "c@d.com"]


def test_smtp_sender_skips_when_recipient_list_empty():
    """Empty `to` is a no-op (with a warning) — not a hard failure."""
    sender = SmtpEmailSender(
        host="h", port=587, username="u", password="p", from_address="from@x.com"
    )
    with patch("sre_free_mcp.email_sender.smtplib.SMTP") as fake_smtp:
        sender.send(EmailMessage(to=[], subject="x", body_text="y"))
    fake_smtp.assert_not_called()


# ---------------------------------------------------------------------------
# default_sender — patch secret manager
# ---------------------------------------------------------------------------


def _smtp_config(secret: str = "sre-smtp") -> EmailConfig:
    return EmailConfig(
        from_address="sre@example.com",
        smtp_host="smtp.example.com",
        smtp_port=587,
        smtp_username="sre@example.com",
        smtp_password_secret=secret,
    )


def test_default_sender_falls_back_to_null_when_no_secret_configured():
    cfg = _smtp_config(secret="")
    sender = default_sender(cfg, project="p")
    assert isinstance(sender, NullEmailSender)


def test_default_sender_builds_smtp_with_secret_value():
    cfg = _smtp_config(secret="sre-smtp")
    with patch("sre_free_mcp.email_sender.get_secret", return_value="resolved-password"):
        sender = default_sender(cfg, project="my-proj")
    assert isinstance(sender, SmtpEmailSender)
    assert sender.password == "resolved-password"
    assert sender.host == "smtp.example.com"


def test_default_sender_calls_secret_manager_with_right_args():
    cfg = _smtp_config(secret="my-secret-name")
    with patch("sre_free_mcp.email_sender.get_secret") as fake_get:
        fake_get.return_value = "x"
        default_sender(cfg, project="my-proj")
    fake_get.assert_called_once_with("my-secret-name", project="my-proj")
