# app/models/sms_blacklist.py

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import Column, DateTime, ForeignKey, Integer, String, Text

from app.database import Base


def utc_now():
    return datetime.now(timezone.utc)


class SMSBlacklist(Base):
    __tablename__ = "sms_blacklist"

    id = Column(Integer, primary_key=True, index=True)

    company_id = Column(
        Integer,
        ForeignKey("companies.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    phone = Column(String(30), nullable=False, index=True)
    reason = Column(Text, nullable=True)

    created_at = Column(DateTime(timezone=True), nullable=False, default=utc_now)
