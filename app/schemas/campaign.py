# app/schemas/campaign.py
from pydantic import BaseModel
from typing import List, Optional
from datetime import datetime
from app.models.sip_trunk import TrunkProvider
from app.models.campaign import CampaignStatus
from app.models.call_log import CallStatus


# SIP Trunk Schemas
class SIPTrunkCreate(BaseModel):
    number: str
    provider: TrunkProvider
    sip_host: str
    sip_username: str
    sip_password: str
    asterisk_endpoint: str
    max_concurrent: Optional[int] = 3

class SIPTrunkResponse(BaseModel):
    id: int
    number: str
    provider: TrunkProvider
    asterisk_endpoint: str
    max_concurrent: int

    class Config:
        from_attributes = True

# Contact Schemas
class ContactCreate(BaseModel):
    phone: str
    full_name: Optional[str] = None
    notes: Optional[str] = None

class ContactResponse(BaseModel):
    id: int
    phone: str
    full_name: Optional[str] = None

    class Config:
        from_attributes = True

class ContactImportResponse(BaseModel):
    filename: str
    created: int
    skipped: int
    total_rows: int

# Audio File Schemas (New)
class AudioFileCreate(BaseModel):
    filename: str
    file_path: str
    duration_sec: Optional[float] = 10.0

class AudioFileResponse(BaseModel):
    id: int
    filename: str
    file_path: str
    company_id: int

    class Config:
        from_attributes = True

# Campaign Schemas
class CampaignCreate(BaseModel):
    name: str
    audio_file_id: int
    contact_ids: Optional[List[int]] = None
    contact_limit: Optional[int] = None

class CampaignResponse(BaseModel):
    id: int
    name: str
    status: CampaignStatus
    total_contacts: int
    target_contact_ids: Optional[List[int]] = None
    created_at: datetime

    class Config:
        from_attributes = True


class CampaignDryRunContact(BaseModel):
    id: int
    phone: str
    full_name: Optional[str] = None

    class Config:
        from_attributes = True


class CampaignDryRunResponse(BaseModel):
    campaign_id: int
    campaign_name: str
    target_count: int
    contacts: List[CampaignDryRunContact]


class CallLogResponse(BaseModel):
    id: int
    phone: str
    status: CallStatus
    duration_sec: Optional[float] = None
    hangup_cause: Optional[int] = None
    answered_at: Optional[datetime] = None
    ended_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class CampaignStatusResponse(BaseModel):
    campaign_id: int
    status: CampaignStatus
    total_contacts: int
    calling: int
    completed: int
    failed: int
    busy: int
    no_answer: int
    congestion: int
    finished: int
    progress_percent: float


class CampaignSimulateResponse(BaseModel):
    campaign_id: int
    simulated: bool = True
    target_count: int
    completed_calls: int


class CampaignRecentCallResponse(BaseModel):
    phone: str
    status: str
    duration_sec: float | None = None
    hangup_cause: int | None = None


class CampaignSummaryResponse(BaseModel):
    campaign_id: int
    name: str
    status: str
    total_contacts: int
    calling: int
    completed: int
    failed: int
    busy: int
    no_answer: int
    congestion: int
    finished: int
    progress_percent: float
    recent_calls: list[CampaignRecentCallResponse]