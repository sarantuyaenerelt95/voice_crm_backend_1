# app/models/company.py

from __future__ import annotations

from sqlalchemy import Column, Integer, String, Boolean, DateTime
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from app.database import Base


class Company(Base):
    __tablename__ = "companies"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(200), nullable=False)
    phone = Column(String(20))                      # company contact phone
    email = Column(String(200), unique=True, nullable=False)
    plan = Column(String(50), default="starter")    # starter / pro / enterprise
    is_active = Column(Boolean, default=True)
    max_contacts = Column(Integer, default=30000)   # contact list size limit
    max_campaigns = Column(Integer, default=1000)   # monthly campaign limit
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())
    audio_storage_limit_mb = Column(Integer, default=200)

    # Call token balance. One token = one answered (billable) call.
    # These are a fast cache of token_ledger and can be rebuilt from it.
    # call_tokens  = tokens owned, including any currently reserved
    # reserved_tokens = held for dials in flight, not yet spent or released
    # spendable = call_tokens - reserved_tokens
    call_tokens = Column(Integer, nullable=False, default=0)
    reserved_tokens = Column(Integer, nullable=False, default=0)

    # relationships
    users = relationship("User", back_populates="company", cascade="all, delete-orphan")
    contacts = relationship("Contact", back_populates="company", cascade="all, delete-orphan")
    campaigns = relationship("Campaign", back_populates="company", cascade="all, delete-orphan")

    def __repr__(self):
        return f"<Company {self.name}>"