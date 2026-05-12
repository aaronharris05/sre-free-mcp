"""Email sender — abstract interface + built-in backends.

The :class:`EmailSender` ABC defines the surface every backend
implements. Two concrete senders ship:

* :class:`SmtpEmailSender` — stdlib ``smtplib`` over STARTTLS. Works
  with Gmail (app password), SendGrid SMTP, AWS SES SMTP, etc.
* :class:`NullEmailSender` — logs the would-be email to stdout. Used
  in tests and as the safe default when no SMTP creds are configured.

To plug in a custom backend (REST API, message bus, Slack, …) inherit
from :class:`EmailSender` and implement :meth:`send`.
"""

from __future__ import annotations

import abc
import logging
import smtplib
from dataclasses import dataclass
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from sre_free_mcp.config import EmailConfig
from sre_free_mcp.secrets import get_secret

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class EmailMessage:
    """One outgoing email.

    ``to`` is always a list — even for one recipient — so multi-
    recipient sends are a YAML edit rather than a code change.
    """

    to: list[str]
    subject: str
    body_text: str
    body_html: str | None = None


class EmailSender(abc.ABC):
    """Abstract email sender. Subclass + implement :meth:`send`."""

    @abc.abstractmethod
    def send(self, message: EmailMessage) -> None:
        """Send one email. Raises on hard failure."""


# ---------------------------------------------------------------------------
# Concrete backends
# ---------------------------------------------------------------------------


class NullEmailSender(EmailSender):
    """Logs the message; sends nothing. Default for tests and dry-runs."""

    def __init__(self) -> None:
        self.sent: list[EmailMessage] = []

    def send(self, message: EmailMessage) -> None:
        self.sent.append(message)
        logger.info(
            "NullEmailSender: would send to=%s subject=%r",
            message.to,
            message.subject,
        )


class SmtpEmailSender(EmailSender):
    """Generic SMTP-over-STARTTLS sender.

    Works with most providers (Gmail app passwords, SendGrid SMTP,
    AWS SES SMTP, custom servers). Password is passed in at construction
    time so the sender is testable without Secret Manager — the factory
    :func:`default_sender` does the Secret Manager lookup.
    """

    def __init__(
        self,
        *,
        host: str,
        port: int,
        username: str,
        password: str,
        from_address: str,
    ) -> None:
        self.host = host
        self.port = port
        self.username = username
        self.password = password
        self.from_address = from_address

    def send(self, message: EmailMessage) -> None:
        if not message.to:
            logger.warning("SmtpEmailSender: empty recipient list; skipping send")
            return

        mime = MIMEMultipart("alternative")
        mime["Subject"] = message.subject
        mime["From"] = self.from_address
        mime["To"] = ", ".join(message.to)
        mime.attach(MIMEText(message.body_text, "plain", "utf-8"))
        if message.body_html:
            mime.attach(MIMEText(message.body_html, "html", "utf-8"))

        with smtplib.SMTP(self.host, self.port) as conn:
            conn.starttls()
            conn.login(self.username, self.password)
            conn.sendmail(self.from_address, message.to, mime.as_string())


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


def default_sender(config: EmailConfig, *, project: str) -> EmailSender:
    """Build the configured sender. Resolves SMTP password from Secret Manager.

    Returns :class:`NullEmailSender` when ``smtp_password_secret`` is
    empty — the safe default for fresh installs where the SMTP secret
    hasn't been populated yet.
    """
    if not config.smtp_password_secret:
        logger.info("default_sender: no smtp_password_secret configured; using NullEmailSender")
        return NullEmailSender()

    password = get_secret(config.smtp_password_secret, project=project)
    return SmtpEmailSender(
        host=config.smtp_host,
        port=config.smtp_port,
        username=config.smtp_username,
        password=password,
        from_address=config.from_address,
    )


__all__ = [
    "EmailMessage",
    "EmailSender",
    "NullEmailSender",
    "SmtpEmailSender",
    "default_sender",
]
