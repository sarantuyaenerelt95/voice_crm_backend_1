# app/config.py
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    DATABASE_URL: str = "postgresql://voice_crm:password@localhost:5432/voice_crm"
    REDIS_URL: str = "redis://localhost:6379/0"
    SECRET_KEY: str = "change-this-in-production"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60 * 24

    ASTERISK_AMI_HOST: str = "127.0.0.1"
    ASTERISK_AMI_PORT: int = 5038
    ASTERISK_AMI_USER: str = "voicebroadcast"
    ASTERISK_AMI_PASS: str = "broadcast123"
    ENABLE_SIMULATION: bool = False 

    class Config:
        env_file = ".env"


settings = Settings()