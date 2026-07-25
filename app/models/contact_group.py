# app/models/contact_group.py

from __future__ import annotations

from sqlalchemy import Column, Integer, String, Text, DateTime, ForeignKey, UniqueConstraint, Boolean, func
from sqlalchemy.orm import relationship

from app.database import Base


class CampaignContactGroup(Base):
    __tablename__ = "campaign_contact_groups"

    id = Column(Integer, primary_key=True, index=True)
    campaign_id = Column(Integer, ForeignKey("campaigns.id", ondelete="CASCADE"), nullable=False, index=True)
    group_id = Column(Integer, ForeignKey("contact_groups.id", ondelete="CASCADE"), nullable=False, index=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    __table_args__ = (
        UniqueConstraint("campaign_id", "group_id", name="uq_campaign_contact_group"),
    )


class ContactGroup(Base):
    __tablename__ = "contact_groups"

    id = Column(Integer, primary_key=True, index=True)
    company_id = Column(Integer, ForeignKey("companies.id"), nullable=False, index=True)

    name = Column(String(255), nullable=False)
    description = Column(Text, nullable=True)
    is_active = Column(Boolean, nullable=False, default=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    members = relationship(
        "ContactGroupMember",
        back_populates="group",
        cascade="all, delete-orphan",
    )

    __table_args__ = (
        UniqueConstraint("company_id", "name", name="uq_contact_groups_company_name"),
    )


class ContactGroupMember(Base):
    __tablename__ = "contact_group_members"

    id = Column(Integer, primary_key=True, index=True)
    group_id = Column(Integer, ForeignKey("contact_groups.id", ondelete="CASCADE"), nullable=False, index=True)
    contact_id = Column(Integer, ForeignKey("contacts.id", ondelete="CASCADE"), nullable=False, index=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    group = relationship("ContactGroup", back_populates="members")

    __table_args__ = (
        UniqueConstraint("group_id", "contact_id", name="uq_contact_group_member"),
    )