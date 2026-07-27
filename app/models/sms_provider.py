# app/models/sms_provider.py

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Integer, String, Text

from app.database import Base


def utc_now():
    return datetime.now(timezone.utc)


class SMSProvider(Base):
    __tablename__ = "sms_providers"

    id = Column(Integer, primary_key=True, index=True)

    company_id = Column(
        Integer,
        ForeignKey("companies.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )

    name = Column(String(120), nullable=False)
    provider_type = Column(String(30), nullable=False, default="simulation")

    sender_name = Column(String(30), nullable=True)

    api_url = Column(Text, nullable=True)
    api_username = Column(String(200), nullable=True)
    api_password = Column(Text, nullable=True)
    api_token = Column(Text, nullable=True)

    is_active = Column(Boolean, nullable=False, default=True)

    created_at = Column(DateTime(timezone=True), nullable=False, default=utc_now)
