# app/models/sms_campaign.py

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import Column, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB

from app.database import Base


def utc_now():
    return datetime.now(timezone.utc)


class SMSCampaign(Base):
    __tablename__ = "sms_campaigns"

    id = Column(Integer, primary_key=True, index=True)

    company_id = Column(
        Integer,
        ForeignKey("companies.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    name = Column(String(200), nullable=False)
    message_text = Column(Text, nullable=False)

    selected_provider_id = Column(
        Integer,
        ForeignKey("sms_providers.id", ondelete="SET NULL"),
        nullable=True,
    )
    
    selected_sip_trunk_id = Column(
        Integer,
        ForeignKey("sip_trunks.id", ondelete="SET NULL"),
        nullable=True,
    )

    target_contact_ids = Column(JSONB, nullable=False, default=list)

    status = Column(String(30), nullable=False, default="draft")

    total_contacts = Column(Integer, nullable=False, default=0)
    sent_count = Column(Integer, nullable=False, default=0)
    delivered_count = Column(Integer, nullable=False, default=0)
    failed_count = Column(Integer, nullable=False, default=0)

    created_by = Column(
        Integer,
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )

    created_at = Column(DateTime(timezone=True), nullable=False, default=utc_now)
    started_at = Column(DateTime(timezone=True), nullable=True)
    completed_at = Column(DateTime(timezone=True), nullable=True)
