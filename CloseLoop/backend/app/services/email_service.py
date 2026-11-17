"""Transactional email — currently just the "forgot password" link.

Sent via plain smtplib (stdlib, no extra dependency; Gmail's smtp.gmail.com
+ an app password works fine over STARTTLS on port 587). Callers dispatch
this as a FastAPI BackgroundTask — a sync function added with
`background_tasks.add_task(...)` runs in Starlette's threadpool, so the
blocking SMTP round-trip never holds up the request/response cycle.
"""

from __future__ import annotations

import logging
import smtplib
from email.message import EmailMessage

from ..config import settings

logger = logging.getLogger(__name__)


def send_password_reset_email(to_email: str, reset_url: str) -> None:
    if not settings.SMTP_HOST or not settings.SMTP_USERNAME:
        # Not configured (e.g. local dev) — log the link instead of failing
        # the request. forgot-password's response is identical either way.
        logger.warning(
            "SMTP not configured; not sending password reset email. "
            "Would have sent to %s: %s",
            to_email,
            reset_url,
        )
        return

    msg = EmailMessage()
    msg["Subject"] = "Reset your CloseLoop password"
    msg["From"] = settings.SMTP_FROM_EMAIL or settings.SMTP_USERNAME
    msg["To"] = to_email
    msg.set_content(
        "We received a request to reset your CloseLoop password.\n\n"
        f"Reset it here (expires in {settings.RESET_TOKEN_EXPIRES_MINUTES} minutes):\n"
        f"{reset_url}\n\n"
        "If you didn't request this, you can ignore this email — your "
        "password will not change."
    )
    msg.add_alternative(
        f"""\
<div style="font-family:-apple-system,Segoe UI,Roboto,sans-serif;max-width:480px;margin:0 auto;color:#1a1a1a;">
  <h2 style="color:#2563eb;margin-bottom:4px;">Reset your CloseLoop password</h2>
  <p>We received a request to reset the password for this account.</p>
  <p style="margin:24px 0;">
    <a href="{reset_url}"
       style="display:inline-block;padding:11px 22px;background:linear-gradient(135deg,#2563eb,#7c3aed);
              color:#fff;border-radius:10px;text-decoration:none;font-weight:500;">
      Reset password
    </a>
  </p>
  <p style="color:#666;font-size:13px;">
    This link expires in {settings.RESET_TOKEN_EXPIRES_MINUTES} minutes and can only be used once.
    If you didn't request this, you can safely ignore this email — your password will not change.
  </p>
</div>
""",
        subtype="html",
    )

    try:
        with smtplib.SMTP(settings.SMTP_HOST, settings.SMTP_PORT, timeout=10) as smtp:
            if settings.SMTP_USE_TLS:
                smtp.starttls()
            smtp.login(settings.SMTP_USERNAME, settings.SMTP_PASSWORD)
            smtp.send_message(msg)
    except Exception:
        # Runs after the HTTP response is already sent — nothing to return
        # the error to. Log it; the token still exists so the user can ask
        # for another email once the underlying issue (bad creds, network)
        # is fixed.
        logger.exception("Failed to send password reset email to %s", to_email)


def send_timesheet_reminder_email(to_email: str, subject: str, body: str) -> None:
    """Plain-text timesheet reminder. Like the reset mail: with SMTP unset it
    logs and returns, so reminders still reach the in-app bell and banner."""
    if not settings.SMTP_HOST or not settings.SMTP_USERNAME:
        logger.info("SMTP not configured; timesheet reminder for %s not e-mailed", to_email)
        return
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = settings.SMTP_FROM_EMAIL or settings.SMTP_USERNAME
    msg["To"] = to_email
    msg.set_content(body)
    try:
        with smtplib.SMTP(settings.SMTP_HOST, settings.SMTP_PORT, timeout=10) as smtp:
            if settings.SMTP_USE_TLS:
                smtp.starttls()
            smtp.login(settings.SMTP_USERNAME, settings.SMTP_PASSWORD)
            smtp.send_message(msg)
    except Exception:  # noqa: BLE001 - a mail failure must never break the reminder run
        logger.exception("Failed to send timesheet reminder to %s", to_email)
