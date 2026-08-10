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
    ASTERISK_SOUNDS_DIR: str = "/var/lib/asterisk/sounds/mn/custom"
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

    # TEMPORARY bridge while the SMTP provider (Brevo) hasn't activated the
    # account yet: if the email fails to send, show the code directly on the
    # page instead of leaving the user with no way to get it. This must be
    # False once real email delivery is confirmed working - it is a debug
    # aid, not something to ship to real users who aren't also the admin
    # testing the feature.
    DEV_SHOW_RESET_CODE_ON_SEND_FAILURE: bool = False

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


settings = Settings()