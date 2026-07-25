# app/models/call_log.py

from __future__ import annotations

import enum

from sqlalchemy import Column, Integer, String, Float, DateTime, ForeignKey, Enum
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from app.database import Base


class CallStatus(str, enum.Enum):
    calling = "calling"          # in progress
    completed = "completed"      # answered and played full message
    failed = "failed"            # could not connect
    busy = "busy"                # line busy or rejected
    no_answer = "no_answer"      # rang but no answer
    congestion = "congestion"    # trunk overloaded


class CallLog(Base):
    __tablename__ = "call_logs"

    id = Column(Integer, primary_key=True, index=True)
    campaign_id = Column(Integer, ForeignKey("campaigns.id"), nullable=False, index=True)
    contact_id = Column(Integer, ForeignKey("contacts.id"), nullable=False, index=True)
    trunk_id = Column(Integer, ForeignKey("sip_trunks.id"), index=True)

    phone = Column(String(20), nullable=False)          # denormalized for speed
    status = Column(Enum(CallStatus), default=CallStatus.calling, index=True)
    duration_sec = Column(Float)                        # how long call lasted
    hangup_cause = Column(Integer)                      # raw Q.931 cause code
    ami_action_id = Column(String(200))                 # AMI ActionID for tracing
    ami_unique_id = Column(String(200))                 # Asterisk Uniqueid

    started_at = Column(DateTime(timezone=True), server_default=func.now())
    answered_at = Column(DateTime(timezone=True))
    ended_at = Column(DateTime(timezone=True))

    # relationships
    campaign = relationship("Campaign", back_populates="call_logs")
    contact = relationship("Contact", back_populates="call_logs")
    trunk = relationship("SIPTrunk", back_populates="call_logs")

    def __repr__(self):
        return f"<CallLog {self.phone} [{self.status}]>"