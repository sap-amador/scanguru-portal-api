"""Minimal, provider-agnostic email sender (SMTP).

Works with any SMTP provider — Amazon SES, SendGrid, Postmark, Mailgun, Gmail —
by setting the SMTP_* config values. If SMTP is not configured, send_email()
logs a warning and returns False instead of raising, so the app never breaks
just because mail isn't wired yet.
"""
from __future__ import annotations
import logging
import smtplib
import ssl
from email.message import EmailMessage

from app.config import settings

log = logging.getLogger("scanguru.mailer")


def _recipients(to) -> list[str]:
    if isinstance(to, str):
        return [x.strip() for x in to.split(",") if x.strip()]
    return [x.strip() for x in to if x and x.strip()]


def send_email(to, subject: str, body_text: str, reply_to: str | None = None) -> bool:
    """Send a plain-text email. Returns True on success, False if unconfigured or failed.

    Never raises — callers treat email as best-effort so a mail outage can't
    take down a request path.
    """
    rcpts = _recipients(to)
    if not settings.smtp_host or not rcpts:
        log.warning("email skipped (smtp_host unset or no recipients): subject=%r", subject)
        return False

    msg = EmailMessage()
    msg["From"] = settings.smtp_from
    msg["To"] = ", ".join(rcpts)
    msg["Subject"] = subject
    if reply_to:
        msg["Reply-To"] = reply_to
    msg.set_content(body_text)

    try:
        if settings.smtp_port == 465:
            ctx = ssl.create_default_context()
            with smtplib.SMTP_SSL(settings.smtp_host, settings.smtp_port, timeout=15, context=ctx) as s:
                if settings.smtp_user:
                    s.login(settings.smtp_user, settings.smtp_password)
                s.send_message(msg)
        else:
            with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=15) as s:
                if settings.smtp_starttls:
                    s.starttls(context=ssl.create_default_context())
                if settings.smtp_user:
                    s.login(settings.smtp_user, settings.smtp_password)
                s.send_message(msg)
        log.info("email sent: subject=%r to=%s", subject, rcpts)
        return True
    except Exception as exc:  # noqa: BLE001 — best-effort; never propagate
        log.error("email send failed: subject=%r error=%s", subject, str(exc)[:200])
        return False
