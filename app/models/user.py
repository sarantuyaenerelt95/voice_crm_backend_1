# app/models/user.py

from __future__ import annotations

import enum

from sqlalchemy import Column, Integer, String, Boolean, DateTime, ForeignKey, Enum
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from app.database import Base


class UserRole(str, enum.Enum):
    owner = "owner"    # full access, can manage users
    admin = "admin"    # can create campaigns, manage contacts
    viewer = "viewer"  # read-only, can see reports


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    company_id = Column(Integer, ForeignKey("companies.id"), nullable=False, index=True)
    email = Column(String(200), unique=True, nullable=False, index=True)
    full_name = Column(String(200))
    hashed_password = Column(String(255), nullable=False)
    role = Column(Enum(UserRole), default=UserRole.admin)
    is_active = Column(Boolean, default=True)
    last_login = Column(DateTime(timezone=True))
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    reset_token_hash = Column(String(64), nullable=True, index=True)
    reset_token_expires_at = Column(DateTime(timezone=True), nullable=True)
    reset_attempts = Column(Integer, nullable=False, default=0)

    # relationships
    company = relationship("Company", back_populates="users")
    campaigns = relationship("Campaign", back_populates="created_by")

    def __repr__(self):
        return f"<User {self.email}>"