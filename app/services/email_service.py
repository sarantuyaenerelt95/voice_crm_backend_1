# app/services/email_service.py

from __future__ import annotations

import smtplib
from email.mime.text import MIMEText

from app.config import settings


_SUBJECTS = {
    "mn": "Voicebro нууц үг сэргээх код",
    "en": "Reset your Voicebro password",
}


def _body(code: str, expiry_minutes: int, language: str) -> str:
    if language == "mn":
        return (
            f"Таны Voicebro бүртгэлийн нууц үг сэргээх хүсэлт ирлээ.\n\n"
            f"Таны код: {code}\n\n"
            f"Энэ кодыг нууц үг сэргээх хуудсанд оруулна уу. Код {expiry_minutes} минутын дараа хүчингүй болно.\n\n"
            f"Хэрэв та энэ хүсэлтийг илгээгээгүй бол энэ и-мэйлийг үл тоомсорлоно уу."
        )

    return (
        f"Someone requested a password reset for your Voicebro account.\n\n"
        f"Your reset code is: {code}\n\n"
        f"Enter this code on the reset page. It expires in {expiry_minutes} minutes.\n\n"
        f"If you did not request this, ignore this email."
    )


def send_password_reset_email(
    to_email: str,
    code: str,
    expiry_minutes: int = 10,
    language: str = "mn",
) -> bool:
    """Send the reset code, in the language the request arrived in.

    Returns False on any failure instead of raising, so a broken SMTP config
    never breaks the request/response flow - the forgot-password page always
    shows the same generic message either way.
    """
    if not settings.SMTP_HOST or not settings.SMTP_USER:
        print("email_service: SMTP not configured, skipping send")
        return False

    language = language if language in _SUBJECTS else "mn"

    msg = MIMEText(_body(code, expiry_minutes, language), _charset="utf-8")
    msg["Subject"] = _SUBJECTS[language]
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
