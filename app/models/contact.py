from sqlalchemy import Column, Integer, String, Boolean, DateTime, ForeignKey, Index, Boolean
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from app.database import Base


class Contact(Base):
    __tablename__ = "contacts"

    id           = Column(Integer, primary_key=True, index=True)
    company_id   = Column(Integer, ForeignKey("companies.id"), nullable=False, index=True)
    phone        = Column(String(20), nullable=False)
    full_name    = Column(String(200))
    notes        = Column(String(500))
    is_active    = Column(Boolean, default=True)    # soft delete / unsubscribe
    created_at   = Column(DateTime(timezone=True), server_default=func.now())
    is_active = Column(Boolean, nullable=False, default=True)

    # relationships
    company   = relationship("Company", back_populates="contacts")
    call_logs = relationship("CallLog", back_populates="contact")

    # one phone per company (no duplicates within same tenant)
    __table_args__ = (
        Index("ix_contacts_company_phone", "company_id", "phone", unique=True),
    )

    def __repr__(self):
        return f"<Contact {self.phone}>"