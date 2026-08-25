# app/config.py

from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    DATABASE_URL: str = "postgresql://voice_crm:password@localhost:5432/voice_crm"
    REDIS_URL: str = "redis://localhost:6379/0"
    SECRET_KEY: str = "change-this-in-production"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60 * 24

    ASTERISK_AMI_HOST: str = "127.0.0.1"
    ASTERISK_AMI_PORT: int = 5038
    ASTERISK_AMI_USER: str = "voicebroadcast"
    ASTERISK_AMI_PASS: str = "change-me"

    # Filesystem locations. These are host paths by default; in a container they
    # must point at mounted volumes shared with Asterisk.
    #
    # ASTERISK_SOUNDS_DIR must sit under Asterisk's astdatadir (/usr/share/asterisk
    # in a default install), NOT astvarlibdir (/var/lib/asterisk). Playback()
    # resolves relative to astdatadir, so audio written under /var/lib is never
    # found: the call answers, plays silence, hangs up, and is still recorded as
    # completed. Check the [directories] block in asterisk.conf before changing.
    ASTERISK_SOUNDS_DIR: str = "/usr/share/asterisk/sounds/mn/custom"
    AUDIO_TEMP_DIR: str = "/tmp"
    TRUNK_CONFIG_FILE: str = "/home/voice_test/voice_crm_backend/runtime/pjsip_voicecrm_trunks.conf"

    # How generated trunk config reaches Asterisk.
    #   script mode : run TRUNK_APPLY_SCRIPT via sudo (needs a local Asterisk host)
    #   native mode : set TRUNK_APPLY_SCRIPT="" and this service writes into
    #                 ASTERISK_CONFIG_DIR itself, then reloads over AMI.
    #                 Use this in containers, with /etc/asterisk mounted writable.
    TRUNK_APPLY_SCRIPT: str = "/usr/local/bin/voicecrm_apply_trunks.sh"
    ASTERISK_CONFIG_DIR: str = "/etc/asterisk"
    TRUNK_INCLUDE_FILENAME: str = "pjsip_voicecrm_trunks.conf"

    ENABLE_SIMULATION: bool = False

    # Speech-to-text service (separate app, port 8002). Called server-side only
    # so its API key never reaches the browser: a logged-in Voicebro session is
    # what authorizes the call, not this key.
    STT_INTERNAL_URL: str = "http://127.0.0.1:8002"
    STT_API_KEY: str = ""

    # Password reset email. Empty SMTP_HOST/SMTP_USER means sending is skipped
    # (email_service logs it and returns False) rather than erroring, so the
    # forgot-password page always behaves the same regardless of whether email
    # is configured yet.
    SMTP_HOST: str = ""
    SMTP_PORT: int = 587
    SMTP_USER: str = ""
    SMTP_PASSWORD: str = ""
    SMTP_FROM_EMAIL: str = "noreply@voicebro.local"
    SMTP_USE_TLS: bool = True
    PASSWORD_RESET_BASE_URL: str = "http://64.119.31.106:8001"

    # The address search engines and link previews should use. Kept separate
    # from PASSWORD_RESET_BASE_URL so the public canonical URL does not change
    # if reset links ever have to point somewhere else.
    PUBLIC_BASE_URL: str = "https://voicebro.mn"

    # QPay V2 merchant API. Credentials come from the merchant onboarding mail
    # and live only in .env, which is gitignored.
    QPAY_BASE_URL: str = "https://merchant.qpay.mn"
    QPAY_CLIENT_ID: str = ""
    QPAY_CLIENT_SECRET: str = ""
    QPAY_INVOICE_CODE: str = ""
    # Where QPay sends the "this invoice was paid" ping. Must be reachable
    # from the public internet and must not sit behind the login redirect.
    QPAY_CALLBACK_BASE: str = "https://voicebro.mn"

    # Debug aid for when outbound email is broken: if the send fails, log the
    # reset code to the container log so an admin with server access can still
    # complete a reset. It is written ONLY to the server log - never to the
    # HTTP response, because putting it on the page would let anyone who knows
    # a user's email pull that account's reset code without inbox access.
    #
    # Added while Brevo had not activated the SMTP account. Outbound mail now
    # goes through Gmail and works, so this should stay False.
    DEV_SHOW_RESET_CODE_ON_SEND_FAILURE: bool = False

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


settings = Settings()