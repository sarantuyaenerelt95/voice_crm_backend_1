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
    TRUNK_APPLY_SCRIPT: str = "/usr/local/bin/voicecrm_apply_trunks.sh"

    # Path to the Asterisk CLI binary used for status queries.
    ASTERISK_BIN: str = "/usr/sbin/asterisk"
    ASTERISK_USE_SUDO: bool = True

    ENABLE_SIMULATION: bool = False

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


settings = Settings()