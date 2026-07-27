# app/models/sms_log.py

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import Column, DateTime, ForeignKey, Integer, String, Text

from app.database import Base


def utc_now():
    return datetime.now(timezone.utc)


class SMSLog(Base):
    __tablename__ = "sms_logs"

    id = Column(Integer, primary_key=True, index=True)

    campaign_id = Column(
        Integer,
        ForeignKey("sms_campaigns.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    contact_id = Column(
        Integer,
        ForeignKey("contacts.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    provider_id = Column(Integer, nullable=True)
    
    sip_trunk_id = Column(
        Integer,
        ForeignKey("sip_trunks.id", ondelete="SET NULL"),
        nullable=True,
    )

    phone = Column(String(30), nullable=False, index=True)
    message_text = Column(Text, nullable=False)

    status = Column(String(30), nullable=False, default="pending")

    provider_message_id = Column(String(200), nullable=True)
    provider_response = Column(Text, nullable=True)
    error_message = Column(Text, nullable=True)

    created_at = Column(DateTime(timezone=True), nullable=False, default=utc_now)
    sent_at = Column(DateTime(timezone=True), nullable=True)
    delivered_at = Column(DateTime(timezone=True), nullable=True)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=utc_now)
