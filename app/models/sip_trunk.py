# app/models/sip_trunk.py

from __future__ import annotations

import enum

from sqlalchemy import Boolean, Column, DateTime, Enum, ForeignKey, Integer, String, Text
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from app.database import Base


class TrunkProvider(str, enum.Enum):
    mobinet = "mobinet"
    cally = "cally"
    unitel = "unitel"
    mobicom = "mobicom"
    other = "other"


class SIPTrunk(Base):
    __tablename__ = "sip_trunks"

    id = Column(Integer, primary_key=True, index=True)
    number = Column(String(20), unique=True, nullable=False)
    provider = Column(Enum(TrunkProvider), nullable=False)
    sip_host = Column(String(200), nullable=False)
    sip_username = Column(String(100), nullable=False)
    sip_password = Column(String(200), nullable=False)
    asterisk_endpoint = Column(String(100), nullable=False)
    max_concurrent = Column(Integer, default=3)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    managed_by_crm = Column(Boolean, nullable=False, default=True)
    assigned_company_id = Column(Integer, nullable=True)
    current_active_calls = Column(Integer, nullable=False, default=0)
    is_applied = Column(Boolean, nullable=False, default=False)

    sms_enabled = Column(Boolean, nullable=False, default=False)
    sms_mode = Column(String(30), nullable=False, default="simulation")
    sms_sender_name = Column(String(30), nullable=True)

    sms_api_url = Column(Text, nullable=True)
    sms_api_username = Column(String(200), nullable=True)
    sms_api_password = Column(Text, nullable=True)
    sms_api_token = Column(Text, nullable=True)
    sms_last_test_status = Column(String(30), nullable=True)
    sms_last_test_error = Column(Text, nullable=True)
    sms_last_test_at = Column(DateTime(timezone=True), nullable=True)

    # relationships
    call_logs = relationship("CallLog", back_populates="trunk")

    def __repr__(self):
        return f"<SIPTrunk {self.number} ({self.provider})>"