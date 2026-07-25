# app/models/campaign.py

from __future__ import annotations

import enum

from sqlalchemy import Column, Integer, String, Boolean, DateTime, ForeignKey, Enum, JSON
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from app.database import Base


class CampaignStatus(str, enum.Enum):
    draft = "draft"          # created, not started
    queued = "queued"        # submitted to Celery
    running = "running"      # calls in progress
    completed = "completed"  # all calls done
    failed = "failed"        # something went wrong
    cancelled = "cancelled"  # manually stopped


class Campaign(Base):
    __tablename__ = "campaigns"

    id = Column(Integer, primary_key=True, index=True)
    company_id = Column(Integer, ForeignKey("companies.id"), nullable=False, index=True)
    created_by_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    audio_file_id = Column(Integer, ForeignKey("audio_files.id"), nullable=False)

    name = Column(String(200), nullable=False)
    status = Column(Enum(CampaignStatus), default=CampaignStatus.draft, index=True)

    # stats — updated as calls complete
    total_contacts = Column(Integer, default=0)
    target_contact_ids = Column(JSON)  # snapshot of contact ids selected for this campaign
    completed_calls = Column(Integer, default=0)
    failed_calls = Column(Integer, default=0)
    busy_calls = Column(Integer, default=0)
    no_answer_calls = Column(Integer, default=0)

    # timing
    scheduled_at = Column(DateTime(timezone=True))  # null = run immediately
    started_at = Column(DateTime(timezone=True))
    completed_at = Column(DateTime(timezone=True))
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    # celery task id for cancellation
    celery_task_id = Column(String(200))

    is_archived = Column(Boolean, nullable=False, default=False)
    selected_sip_trunk_id = Column(Integer, nullable=True)

    # relationships
    company = relationship("Company", back_populates="campaigns")
    created_by = relationship("User", back_populates="campaigns")
    audio_file = relationship("AudioFile", back_populates="campaigns")
    call_logs = relationship("CallLog", back_populates="campaign", cascade="all, delete-orphan")
    targets = relationship("CampaignTarget", back_populates="campaign", cascade="all, delete-orphan")

    def __repr__(self):
        return f"<Campaign {self.name} [{self.status}]>"