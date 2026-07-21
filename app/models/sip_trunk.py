from sqlalchemy import Column, Integer, String, Boolean, DateTime, Enum
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
import enum
from app.database import Base


class TrunkProvider(str, enum.Enum):
    mobinet = "mobinet"
    cally   = "cally"
    unitel  = "unitel"
    mobicom = "mobicom"
    other   = "other"


class SIPTrunk(Base):
    __tablename__ = "sip_trunks"

    id           = Column(Integer, primary_key=True, index=True)
    number       = Column(String(20), unique=True, nullable=False)  # e.g. 75350957
    provider     = Column(Enum(TrunkProvider), nullable=False)
    sip_host     = Column(String(200), nullable=False)              # e.g. 202.131.253.177
    sip_username = Column(String(100), nullable=False)
    sip_password = Column(String(200), nullable=False)              # encrypt in production
    asterisk_endpoint = Column(String(100), nullable=False)         # pjsip endpoint name e.g. "mobinet"
    max_concurrent    = Column(Integer, default=3)                  # trunk call limit
    is_active    = Column(Boolean, default=True)
    created_at   = Column(DateTime(timezone=True), server_default=func.now())
    managed_by_crm = Column(Boolean, nullable=False, default=True)
    assigned_company_id = Column(Integer, nullable=True)
    current_active_calls = Column(Integer, nullable=False, default=0)
    is_applied = Column(Boolean, nullable=False, default=False)

    # relationships
    call_logs = relationship("CallLog", back_populates="trunk")

    def __repr__(self):
        return f"<SIPTrunk {self.number} ({self.provider})>"