# app/services/email_service.py

from __future__ import annotations

import smtplib
from email.mime.text import MIMEText

from app.config import settings


def send_password_reset_email(to_email: str, code: str) -> bool:
    """Send the reset code. Returns False on any failure instead of raising,
    so a broken SMTP config never breaks the request/response flow - the
    forgot-password page always shows the same generic message either way.
    """
    if not settings.SMTP_HOST or not settings.SMTP_USER:
        print("email_service: SMTP not configured, skipping send")
        return False

    body = (
        f"Someone requested a password reset for your Voicebro account.\n\n"
        f"Your reset code is: {code}\n\n"
        f"Enter this code on the reset page. It expires in 10 minutes.\n\n"
        f"If you did not request this, ignore this email."
    )

    msg = MIMEText(body)
    msg["Subject"] = "Reset your Voicebro password"
    msg["From"] = settings.SMTP_FROM_EMAIL
    msg["To"] = to_email

    try:
        with smtplib.SMTP(settings.SMTP_HOST, settings.SMTP_PORT, timeout=10) as server:
            if settings.SMTP_USE_TLS:
                server.starttls()
            server.login(settings.SMTP_USER, settings.SMTP_PASSWORD)
            server.sendmail(settings.SMTP_FROM_EMAIL, [to_email], msg.as_string())
        return True

    except Exception as exc:
        print(f"email_service: failed to send reset email to {to_email}: {exc}")
        return False
