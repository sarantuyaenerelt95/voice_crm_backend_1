# app/models/campaign_target.py

from __future__ import annotations

from sqlalchemy import Column, Integer, String, DateTime, ForeignKey
from sqlalchemy.sql import func
from sqlalchemy.orm import relationship

from app.database import Base


class CampaignTarget(Base):
    __tablename__ = "campaign_targets"

    id = Column(Integer, primary_key=True, index=True)
    campaign_id = Column(Integer, ForeignKey("campaigns.id"), nullable=False, index=True)
    contact_id = Column(Integer, ForeignKey("contacts.id"), nullable=True, index=True)
    phone = Column(String(32), nullable=False, index=True)
    position = Column(Integer, nullable=False, default=0)
    status = Column(String(32), nullable=False, default="pending", index=True)
    call_log_id = Column(Integer, ForeignKey("call_logs.id"), nullable=True, index=True)
    attempts = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())

    campaign = relationship("Campaign", back_populates="targets")
    contact = relationship("Contact")
    call_log = relationship("CallLog")