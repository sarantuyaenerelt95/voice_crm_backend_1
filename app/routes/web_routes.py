# app/routes/web_routes.py

from __future__ import annotations

from typing import Optional
import csv
import io
import json
import re
import os
import time
import shutil

import qrcode
import qrcode.image.svg
import requests

from app.services.tts_service import TTSService, DEFAULT_TTS_VOICE

from pathlib import Path
from datetime import datetime, timezone

from fastapi import APIRouter, Request, Depends, HTTPException, Form, Response, UploadFile, File, Query
from fastapi.responses import (
    FileResponse,
    HTMLResponse,
    JSONResponse,
    RedirectResponse,
    StreamingResponse,
)
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from sqlalchemy import func, or_

from app.config import settings
from app.database import get_db
from app.models.campaign import Campaign, CampaignStatus
from app.models.call_log import CallLog, CallStatus
from app.models.audio_file import AudioFile, AudioSource
from app.models.contact import Contact
from app.models.user import User
from app.models.company import Company

from app.models.campaign_target import CampaignTarget
from app.models.contact_group import ContactGroup, ContactGroupMember, CampaignContactGroup
from app.models.billing import PurchaseStatus, TokenPackage, TokenPurchase

from app.services import billing_service, qpay_service
from app.services.audio_converter import AudioConverter
from app.services.sip_availability import get_available_sip_rows
from app.services.campaign_target_service import sync_campaign_targets_from_contact_ids
from app.services.audio_capacity import (
    check_audio_duration,
    check_audio_storage_capacity,
    safe_remove_file,
)

from app.routes import payment_routes
from app.tasks.campaign_tasks import run_campaign_task
from app.routes.web_auth_routes import (
    hash_password,
    normalized_role,
    user_password_column,
    verify_password,
)


router = APIRouter(prefix="/web", tags=["web"])
templates = Jinja2Templates(directory="app/templates")

ASTERISK_SOUNDS_DIR = settings.ASTERISK_SOUNDS_DIR
ALLOWED_EXTENSIONS = {".mp3", ".wav", ".m4a", ".ogg", ".flac", ".gsm"}

# What MediaRecorder can hand us: Chrome/Edge produce webm/opus, Firefox ogg,
# Safari mp4/aac. The container is irrelevant after ffmpeg converts it.
RECORDING_EXTENSIONS = {".webm", ".ogg", ".oga", ".mp4", ".m4a", ".wav", ".mp3"}

RECORDING_MIME_EXTENSIONS = {
    "audio/webm": ".webm",
    "video/webm": ".webm",
    "audio/ogg": ".ogg",
    "audio/mp4": ".mp4",
    "video/mp4": ".mp4",
    "audio/mpeg": ".mp3",
    "audio/wav": ".wav",
    "audio/x-wav": ".wav",
    "audio/wave": ".wav",
}

# A trim shorter than this is almost always a mis-drag rather than an edit.
MIN_TRIM_DURATION_SEC = 0.5

# Fixed codes instead of free text so nothing a caller puts in the query
# string ends up rendered back on the page.
AUDIO_PAGE_NOTICES = {
    "renamed": "Audio name updated.",
    "trimmed": "Trimmed copy saved, and the original was removed from the library.",
    "trimmed_kept": (
        "Trimmed copy saved. The original is still attached to a campaign, "
        "so it was kept in the library."
    ),
    "trimmed_new": "Trimmed copy saved as a new audio file.",
}


def safe_audio_name(filename: str) -> str:
    base = Path(filename).stem.lower()
    base = re.sub(r"[^a-z0-9_]+", "_", base)
    return base.strip("_") or "audio"


def active_audio_files(db: Session, company_id: int):
    return db.query(AudioFile).filter(
        AudioFile.company_id == company_id,
        AudioFile.is_active == True,
    ).order_by(
        AudioFile.created_at.asc(),
        AudioFile.id.asc(),
    ).all()


def get_company_audio(
    db: Session,
    company_id: int,
    audio_id: int,
    active_only: bool = True,
) -> AudioFile:
    query = db.query(AudioFile).filter(
        AudioFile.id == audio_id,
        AudioFile.company_id == company_id,
    )

    if active_only:
        query = query.filter(AudioFile.is_active == True)

    audio = query.first()

    if not audio:
        raise HTTPException(status_code=404, detail="Audio file not found")

    return audio


def register_audio_file(
    db: Session,
    company_id: int,
    unique_name: str,
    output_path: str,
    source: AudioSource,
    tts_text: Optional[str] = None,
    display_name: Optional[str] = None,
) -> AudioFile:
    """Measure a freshly converted WAV, enforce the quotas, and store the row.

    Shared by upload, text-to-speech, recording and trimming so all four apply
    the same duration and storage limits.
    """
    os.chmod(output_path, 0o644)

    duration_sec = AudioConverter.get_duration_sec(output_path)
    check_audio_duration(duration_sec)

    file_size_bytes = os.path.getsize(output_path)
    check_audio_storage_capacity(db, company_id, file_size_bytes)

    audio = AudioFile(
        company_id=company_id,
        filename=unique_name,
        display_name=(display_name or None),
        file_path=output_path,
        source=source,
        tts_text=tts_text,
        duration_sec=duration_sec,
        file_size_bytes=file_size_bytes,
        is_active=True,
    )

    db.add(audio)
    db.commit()
    db.refresh(audio)

    return audio


def audio_result_payload(db: Session, company_id: int, audio: AudioFile) -> dict:
    """The "here is what you just saved" banner data for the audio page."""
    display_map = {
        audio_item.id: index
        for index, audio_item in enumerate(active_audio_files(db, company_id), start=1)
    }

    return {
        "id": audio.id,
        "display_id": display_map.get(audio.id, 0),
        "filename": audio.label,
        "duration_sec": audio.duration_sec,
        "playback_name": audio.playback_name,
    }


def has_model_column(model, column_name: str) -> bool:
    return column_name in {column.name for column in model.__table__.columns}


def get_current_web_user(request: Request, db: Session) -> User:
    user_id = request.session.get("user_id")

    if not user_id:
        raise HTTPException(status_code=401, detail="Not logged in")

    user = db.query(User).filter(User.id == user_id).first()

    if not user:
        request.session.clear()
        raise HTTPException(status_code=401, detail="Invalid session")

    role_value = normalized_role(user)

    if role_value == "owner":
        raise HTTPException(
            status_code=303,
            detail="Owner can only manage SIP numbers",
            headers={"Location": "/admin/sip-numbers"},
        )

    return user


def profile_context(
    request: Request,
    user: User,
    db: Session,
    message: Optional[str] = None,
    error: Optional[str] = None,
):
    company = db.query(Company).filter(
        Company.id == user.company_id,
    ).first()

    company_users = db.query(User).filter(
        User.company_id == user.company_id,
    ).order_by(
        User.id.asc(),
    ).all()

    role_value = normalized_role(user)

    token_balance = billing_service.get_balance(db, user.company_id)

    token_packages = db.query(TokenPackage).filter(
        TokenPackage.is_active == True,
    ).order_by(
        TokenPackage.sort_order.asc(),
    ).all()

    recent_purchases = db.query(TokenPurchase).filter(
        TokenPurchase.company_id == user.company_id,
    ).order_by(
        TokenPurchase.id.desc(),
    ).limit(10).all()

    return {
        "request": request,
        "user": user,
        "company": company,
        "company_users": company_users,
        "role_value": role_value,
        "token_balance": token_balance,
        "token_packages": token_packages,
        "recent_purchases": recent_purchases,
        "can_edit_company": role_value == "admin",
        "user_has_phone": has_model_column(User, "phone"),
        "message": message,
        "error": error,
        "contact_count": db.query(Contact).filter(Contact.company_id == user.company_id).count(),
        "active_contact_count": db.query(Contact).filter(
            Contact.company_id == user.company_id,
            Contact.is_active == True,
        ).count(),
        "campaign_count": db.query(Campaign).filter(Campaign.company_id == user.company_id).count(),
        "audio_count": db.query(AudioFile).filter(AudioFile.company_id == user.company_id).count(),
    }


def render_profile(
    request: Request,
    user: User,
    db: Session,
    message: Optional[str] = None,
    error: Optional[str] = None,
    status_code: int = 200,
):
    return templates.TemplateResponse(
        "profile.html",
        profile_context(request, user, db, message=message, error=error),
        status_code=status_code,
    )


def usable_sip_rows_for_company(db: Session, company_id: int):
    rows = get_available_sip_rows(db, company_id)

    return [
        row
        for row in rows
        if row.get("available")
        and str(row.get("register_status") or "").strip() == "Registered"
        and int(row.get("free_slots") or 0) > 0
    ]


def campaign_status_value(campaign: Campaign) -> str:
    raw_status = campaign.status

    if hasattr(raw_status, "value"):
        return str(raw_status.value)

    value = str(raw_status or "").lower().strip()

    if "." in value:
        value = value.split(".")[-1]

    return value


def get_campaign_target_count(db: Session, campaign: Campaign) -> int:
    target_count = db.query(func.count(CampaignTarget.id)).filter(
        CampaignTarget.campaign_id == campaign.id,
    ).scalar() or 0

    if target_count > 0:
        return int(target_count)

    return len(campaign.target_contact_ids or [])


def campaign_start_disabled_reason(campaign: Campaign, sip_rows, db: Session) -> Optional[str]:
    if campaign_status_value(campaign) != "draft":
        return "Only draft campaigns can be started."

    if get_campaign_target_count(db, campaign) <= 0:
        return "Add at least one target contact before starting."

    if not campaign.audio_file:
        return "Attach an audio file before starting."

    if not sip_rows:
        return "No registered SIP number with free call slots is available."

    return None


def without_archived_campaigns(query):
    if has_model_column(Campaign, "is_archived"):
        return query.filter(getattr(Campaign, "is_archived") == False)

    return query


def build_campaign_kwargs(**kwargs):
    if has_model_column(Campaign, "is_archived"):
        kwargs["is_archived"] = False

    return kwargs


def get_target_contacts(campaign: Campaign, db: Session):
    target_ids = campaign.target_contact_ids or []

    if not target_ids:
        return []

    contacts = db.query(Contact).filter(
        Contact.company_id == campaign.company_id,
        Contact.is_active == True,
        Contact.id.in_(target_ids),
    ).all()

    contact_map = {contact.id: contact for contact in contacts}

    return [
        contact_map[contact_id]
        for contact_id in target_ids
        if contact_id in contact_map
    ]


def get_company_campaign_display_id(campaign: Campaign, db: Session) -> int:
    company_campaigns_query = db.query(Campaign.id).filter(
        Campaign.company_id == campaign.company_id,
    )

    company_campaigns = without_archived_campaigns(company_campaigns_query).order_by(
        Campaign.created_at.asc(),
        Campaign.id.asc(),
    ).all()

    campaign_display_map = {
        row.id: index
        for index, row in enumerate(company_campaigns)
    }

    return campaign_display_map.get(campaign.id, 0)


@router.get("/dashboard", response_class=HTMLResponse)
def web_dashboard(
    request: Request,
    db: Session = Depends(get_db),
):
    user = get_current_web_user(request, db)

    campaign_query = without_archived_campaigns(
        db.query(Campaign).filter(Campaign.company_id == user.company_id)
    )

    return templates.TemplateResponse(
        "dashboard.html",
        {
            "request": request,
            "user": user,
            "company_name": user.company.name if user.company else "your company",
            "total_campaigns": campaign_query.count(),
            "draft_campaigns": campaign_query.filter(Campaign.status == CampaignStatus.draft).count(),
            "running_campaigns": campaign_query.filter(
                Campaign.status.in_([CampaignStatus.queued, CampaignStatus.running])
            ).count(),
            "contact_count": db.query(Contact).filter(
                Contact.company_id == user.company_id,
                Contact.is_active == True,
            ).count(),
            "audio_count": db.query(AudioFile).filter(
                AudioFile.company_id == user.company_id,
                AudioFile.is_active == True,
            ).count(),
        },
    )


@router.get("/campaigns", response_class=HTMLResponse)
def web_campaigns(
    request: Request,
    db: Session = Depends(get_db),
):
    user = get_current_web_user(request, db)

    campaign_query = db.query(Campaign).filter(
        Campaign.company_id == user.company_id,
    )

    campaigns = without_archived_campaigns(campaign_query).order_by(
        Campaign.created_at.asc(),
        Campaign.id.asc(),
    ).all()

    return templates.TemplateResponse(
        "campaigns.html",
        {
            "request": request,
            "user": user,
            "company_name": user.company.name if user.company else "your company",
            "campaigns": campaigns,
        },
    )


@router.get("/profile", response_class=HTMLResponse)
def web_profile(
    request: Request,
    db: Session = Depends(get_db),
):
    user = get_current_web_user(request, db)
    return render_profile(request, user, db)


@router.post("/profile", response_class=HTMLResponse)
async def web_update_profile(
    request: Request,
    db: Session = Depends(get_db),
):
    user = get_current_web_user(request, db)
    form = await request.form()

    full_name = (form.get("full_name") or "").strip()
    email = (form.get("email") or "").strip().lower()

    if not email:
        return render_profile(
            request,
            user,
            db,
            error="Email is required.",
            status_code=400,
        )

    existing_user = db.query(User).filter(
        User.email == email,
        User.id != user.id,
    ).first()

    if existing_user:
        return render_profile(
            request,
            user,
            db,
            error="That email is already used by another account.",
            status_code=400,
        )

    user.full_name = full_name or None
    user.email = email

    if has_model_column(User, "phone"):
        setattr(user, "phone", (form.get("phone") or "").strip() or None)

    db.commit()
    db.refresh(user)

    return render_profile(
        request,
        user,
        db,
        message="Profile updated.",
    )


@router.post("/profile/password", response_class=HTMLResponse)
async def web_update_profile_password(
    request: Request,
    db: Session = Depends(get_db),
):
    user = get_current_web_user(request, db)
    form = await request.form()

    current_password = str(form.get("current_password") or "")
    new_password = str(form.get("new_password") or "")
    confirm_password = str(form.get("confirm_password") or "")

    if len(new_password) < 6:
        return render_profile(
            request,
            user,
            db,
            error="New password must be at least 6 characters.",
            status_code=400,
        )

    if new_password != confirm_password:
        return render_profile(
            request,
            user,
            db,
            error="New password and confirmation do not match.",
            status_code=400,
        )

    password_col = user_password_column()
    stored_hash = getattr(user, password_col, "")

    try:
        password_ok = bool(stored_hash) and verify_password(current_password, stored_hash)
    except Exception:
        password_ok = False

    if not password_ok:
        return render_profile(
            request,
            user,
            db,
            error="Current password is incorrect.",
            status_code=400,
        )

    try:
        setattr(user, password_col, hash_password(new_password))
    except ValueError as exc:
        return render_profile(
            request,
            user,
            db,
            error=str(exc),
            status_code=400,
        )

    db.commit()

    return render_profile(
        request,
        user,
        db,
        message="Password changed.",
    )


@router.post("/profile/company", response_class=HTMLResponse)
async def web_update_company_profile(
    request: Request,
    db: Session = Depends(get_db),
):
    user = get_current_web_user(request, db)

    if normalized_role(user) != "admin":
        return render_profile(
            request,
            user,
            db,
            error="Only company admins can update company information.",
            status_code=403,
        )

    form = await request.form()
    company = db.query(Company).filter(
        Company.id == user.company_id,
    ).first()

    if not company:
        return render_profile(
            request,
            user,
            db,
            error="Company record was not found.",
            status_code=404,
        )

    company_name = (form.get("company_name") or "").strip()
    company_email = (form.get("company_email") or "").strip().lower()
    company_phone = (form.get("company_phone") or "").strip()

    if not company_name:
        return render_profile(
            request,
            user,
            db,
            error="Company name is required.",
            status_code=400,
        )

    if not company_email:
        return render_profile(
            request,
            user,
            db,
            error="Company email is required.",
            status_code=400,
        )

    existing_company = db.query(Company).filter(
        Company.email == company_email,
        Company.id != company.id,
    ).first()

    if existing_company:
        return render_profile(
            request,
            user,
            db,
            error="That company email is already used by another company.",
            status_code=400,
        )

    company.name = company_name
    company.email = company_email
    company.phone = company_phone or None

    db.commit()
    db.refresh(company)

    return render_profile(
        request,
        user,
        db,
        message="Company information updated.",
    )


@router.get("/campaigns/new", response_class=HTMLResponse)
def web_new_campaign(
    request: Request,
    db: Session = Depends(get_db),
):
    user = get_current_web_user(request, db)

    audio_files = active_audio_files(db, user.company_id)

    active_contacts_count = db.query(Contact).filter(
        Contact.company_id == user.company_id,
        Contact.is_active == True,
    ).count()

    contacts = db.query(Contact).filter(
        Contact.company_id == user.company_id,
        Contact.is_active == True,
    ).order_by(
        Contact.id.asc(),
    ).all()

    group_rows = db.query(
        ContactGroup,
        func.count(ContactGroupMember.id).label("member_count"),
    ).outerjoin(
        ContactGroupMember,
        ContactGroup.id == ContactGroupMember.group_id,
    ).filter(
        ContactGroup.company_id == user.company_id,
        ContactGroup.is_active == True,
    ).group_by(
        ContactGroup.id,
    ).order_by(
        ContactGroup.created_at.asc(),
        ContactGroup.id.asc(),
    ).all()

    group_ids = [group.id for group, member_count in group_rows]
    group_member_phone_map = {group_id: [] for group_id in group_ids}

    if group_ids:
        member_phone_rows = db.query(
            ContactGroupMember.group_id,
            Contact.phone,
        ).join(
            Contact,
            Contact.id == ContactGroupMember.contact_id,
        ).filter(
            ContactGroupMember.group_id.in_(group_ids),
            Contact.company_id == user.company_id,
            Contact.is_active == True,
        ).order_by(
            ContactGroupMember.group_id.asc(),
            Contact.id.asc(),
        ).all()

        seen_group_phones = {group_id: set() for group_id in group_ids}

        for group_id, phone in member_phone_rows:
            normalized_phone = re.sub(r"\D", "", phone or "")

            if not normalized_phone or normalized_phone in seen_group_phones.setdefault(group_id, set()):
                continue

            group_member_phone_map.setdefault(group_id, []).append(normalized_phone)
            seen_group_phones[group_id].add(normalized_phone)

    contact_groups = [
        {
            "group": group,
            "member_count": member_count,
            "member_phones": group_member_phone_map.get(group.id, []),
        }
        for group, member_count in group_rows
    ]

    return templates.TemplateResponse(
        "campaign_new.html",
        {
            "request": request,
            "audio_files": audio_files,
            "active_contacts_count": active_contacts_count,
            "contacts": contacts,
            "contact_groups": contact_groups,
        },
    )


@router.post("/campaigns/new")
async def web_create_campaign(
    request: Request,
    db: Session = Depends(get_db),
):
    form = await request.form()
    user = get_current_web_user(request, db)

    name = (form.get("name") or "").strip()
    audio_file_id = int(form.get("audio_file_id") or 0)

    single_phone = (form.get("single_phone") or "").strip()
    single_name = (form.get("single_name") or "").strip()
    bulk_numbers = (form.get("bulk_numbers") or "").strip()

    contact_ids_raw = form.getlist("contact_ids")
    group_ids_raw = form.getlist("group_ids")

    contact_ids = []
    for value in contact_ids_raw:
        try:
            contact_ids.append(int(value))
        except ValueError:
            pass

    group_ids = []
    for value in group_ids_raw:
        try:
            group_ids.append(int(value))
        except ValueError:
            pass

    contact_file = form.get("contact_file")

    if not name:
        raise HTTPException(status_code=400, detail="Campaign name is required")

    audio = db.query(AudioFile).filter(
        AudioFile.id == audio_file_id,
        AudioFile.company_id == user.company_id,
        AudioFile.is_active == True,
    ).first()

    if not audio:
        raise HTTPException(status_code=404, detail="Audio file not found")

    target_contact_ids = []
    seen_target_ids = set()
    seen_target_phones = set()

    def normalize_campaign_phone(value: Optional[str]) -> str:
        return re.sub(r"\D", "", value or "")

    def add_target(contact: Optional[Contact]):
        if not contact:
            return

        phone = normalize_campaign_phone(contact.phone)

        if len(phone) != 8:
            return

        if phone in seen_target_phones or contact.id in seen_target_ids:
            return

        target_contact_ids.append(contact.id)
        seen_target_ids.add(contact.id)
        seen_target_phones.add(phone)

    def get_or_create_contact(phone_value: str, full_name: Optional[str] = None):
        phone = normalize_campaign_phone(phone_value)

        if len(phone) != 8:
            return None

        contact = db.query(Contact).filter(
            Contact.company_id == user.company_id,
            Contact.phone == phone,
        ).order_by(
            Contact.is_active.desc(),
            Contact.id.asc(),
        ).first()

        if contact:
            if not contact.is_active:
                contact.is_active = True
            return contact

        contact = Contact(
            company_id=user.company_id,
            phone=phone,
            full_name=full_name or None,
            notes=None,
            is_active=True,
        )

        db.add(contact)
        db.flush()

        return contact

    selected_group_ids = []

    if group_ids:
        groups = db.query(ContactGroup).filter(
            ContactGroup.company_id == user.company_id,
            ContactGroup.is_active == True,
            ContactGroup.id.in_(group_ids),
        ).all()

        valid_group_ids = {group.id for group in groups}

        for group_id in group_ids:
            if group_id not in valid_group_ids:
                continue

            selected_group_ids.append(group_id)

            members = db.query(Contact).join(
                ContactGroupMember,
                Contact.id == ContactGroupMember.contact_id,
            ).filter(
                ContactGroupMember.group_id == group_id,
                Contact.company_id == user.company_id,
                Contact.is_active == True,
            ).order_by(
                Contact.id.asc(),
            ).all()

            for contact in members:
                add_target(contact)

    if contact_ids:
        selected_contacts = db.query(Contact).filter(
            Contact.company_id == user.company_id,
            Contact.is_active == True,
            Contact.id.in_(contact_ids),
        ).all()

        selected_map = {contact.id: contact for contact in selected_contacts}

        for contact_id in contact_ids:
            if contact_id in selected_map:
                add_target(selected_map[contact_id])

    if single_phone:
        contact = get_or_create_contact(single_phone, single_name)
        add_target(contact)

    if bulk_numbers:
        parts = re.split(r"[\s,;]+", bulk_numbers)

        for part in parts:
            contact = get_or_create_contact(part)
            add_target(contact)

    if contact_file and getattr(contact_file, "filename", ""):
        filename = contact_file.filename.lower()

        if not (filename.endswith(".csv") or filename.endswith(".txt")):
            raise HTTPException(
                status_code=400,
                detail="Only CSV or TXT contact files are allowed",
            )

        try:
            raw_content = await contact_file.read()
            content = raw_content.decode("utf-8-sig")
        except UnicodeDecodeError:
            raise HTTPException(
                status_code=400,
                detail="Contact file must be UTF-8 encoded",
            )

        if filename.endswith(".txt"):
            for line in content.splitlines():
                contact = get_or_create_contact(line.strip())
                add_target(contact)

        elif filename.endswith(".csv"):
            stream = io.StringIO(content)
            dict_reader = csv.DictReader(stream)

            fieldnames = [
                name.strip().lower()
                for name in (dict_reader.fieldnames or [])
            ]

            if "phone" in fieldnames:
                for row in dict_reader:
                    normalized = {
                        (key or "").strip().lower(): (value or "").strip()
                        for key, value in row.items()
                    }

                    contact = get_or_create_contact(
                        normalized.get("phone", ""),
                        normalized.get("full_name", ""),
                    )
                    add_target(contact)

            else:
                stream.seek(0)
                reader = csv.reader(stream)

                for row in reader:
                    if not row:
                        continue

                    phone = row[0].strip() if len(row) > 0 else ""
                    full_name = row[1].strip() if len(row) > 1 else ""

                    contact = get_or_create_contact(phone, full_name)
                    add_target(contact)

    if not target_contact_ids:
        raise HTTPException(
            status_code=400,
            detail="Please select, add, paste, or import at least one phone number",
        )

    campaign = Campaign(**build_campaign_kwargs(
        company_id=user.company_id,
        created_by_id=user.id,
        audio_file_id=audio.id,
        name=name,
        status=CampaignStatus.draft,
        total_contacts=len(target_contact_ids),
        target_contact_ids=target_contact_ids,
    ))

    db.add(campaign)
    db.flush()

    for group_id in selected_group_ids:
        campaign_group = CampaignContactGroup(
            campaign_id=campaign.id,
            group_id=group_id,
        )
        db.add(campaign_group)

    db.commit()
    db.refresh(campaign)

    target_count = sync_campaign_targets_from_contact_ids(
        db=db,
        campaign=campaign,
        target_contact_ids=target_contact_ids,
    )

    campaign.total_contacts = target_count
    db.commit()
    db.refresh(campaign)

    return RedirectResponse(
        url=f"/web/campaigns/{campaign.id}",
        status_code=303,
    )


@router.get("/contacts/import", response_class=HTMLResponse)
def web_import_contacts_page(
    request: Request,
    db: Session = Depends(get_db),
):
    user = get_current_web_user(request, db)

    total_contacts = db.query(Contact).filter(
        Contact.company_id == user.company_id,
        Contact.is_active == True,
    ).count()

    return templates.TemplateResponse(
        "contacts_import.html",
        {
            "request": request,
            "total_contacts": total_contacts,
            "result": None,
        },
    )


@router.post("/contacts/import", response_class=HTMLResponse)
def web_import_contacts(
    request: Request,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    user = get_current_web_user(request, db)

    filename = (file.filename or "").lower()

    if not (filename.endswith(".csv") or filename.endswith(".txt")):
        raise HTTPException(
            status_code=400,
            detail="Only CSV or TXT file allowed",
        )

    try:
        content = file.file.read().decode("utf-8-sig")
    except UnicodeDecodeError:
        raise HTTPException(status_code=400, detail="File must be UTF-8 encoded")

    rows = []

    if filename.endswith(".csv"):
        stream = io.StringIO(content)
        dict_reader = csv.DictReader(stream)

        fieldnames = [
            name.strip().lower()
            for name in (dict_reader.fieldnames or [])
        ]

        if "phone" in fieldnames:
            for row in dict_reader:
                normalized = {
                    (key or "").strip().lower(): (value or "").strip()
                    for key, value in row.items()
                }

                rows.append({
                    "phone": normalized.get("phone", ""),
                    "full_name": normalized.get("full_name") or None,
                    "notes": normalized.get("notes") or None,
                })
        else:
            stream.seek(0)
            reader = csv.reader(stream)

            for row in reader:
                if not row:
                    continue

                rows.append({
                    "phone": row[0].strip() if len(row) > 0 else "",
                    "full_name": row[1].strip() if len(row) > 1 and row[1].strip() else None,
                    "notes": row[2].strip() if len(row) > 2 and row[2].strip() else None,
                })

    elif filename.endswith(".txt"):
        for line in content.splitlines():
            phone = line.strip()

            if phone:
                rows.append({
                    "phone": phone,
                    "full_name": None,
                    "notes": None,
                })

    existing_phones = {
        phone
        for (phone,) in db.query(Contact.phone).filter(
            Contact.company_id == user.company_id,
        ).all()
    }

    seen_phones = set()
    created = 0
    skipped = 0

    for row in rows:
        phone = re.sub(r"\D", "", row.get("phone") or "")

        if len(phone) != 8 or phone in existing_phones or phone in seen_phones:
            skipped += 1
            continue

        contact = Contact(
            company_id=user.company_id,
            phone=phone,
            full_name=row.get("full_name") or None,
            notes=row.get("notes") or None,
            is_active=True,
        )

        db.add(contact)
        seen_phones.add(phone)
        created += 1

    db.commit()

    total_contacts = db.query(Contact).filter(
        Contact.company_id == user.company_id,
        Contact.is_active == True,
    ).count()

    return templates.TemplateResponse(
        "contacts_import.html",
        {
            "request": request,
            "total_contacts": total_contacts,
            "result": {
                "filename": file.filename,
                "created": created,
                "skipped": skipped,
                "total_rows": len(rows),
            },
        },
    )


@router.get("/contacts", response_class=HTMLResponse)
def web_contacts(
    request: Request,
    db: Session = Depends(get_db),
):
    user = get_current_web_user(request, db)

    q = (request.query_params.get("q") or "").strip()
    status = (request.query_params.get("status") or "all").strip()

    query = db.query(Contact).filter(
        Contact.company_id == user.company_id,
    )

    if status == "active":
        query = query.filter(Contact.is_active == True)
    elif status == "inactive":
        query = query.filter(Contact.is_active == False)

    if q:
        query = query.filter(
            or_(
                Contact.phone.ilike(f"%{q}%"),
                Contact.full_name.ilike(f"%{q}%"),
            )
        )

    contacts = query.order_by(
        Contact.id.asc(),
    ).all()

    total_active = db.query(Contact).filter(
        Contact.company_id == user.company_id,
        Contact.is_active == True,
    ).count()

    total_inactive = db.query(Contact).filter(
        Contact.company_id == user.company_id,
        Contact.is_active == False,
    ).count()

    return templates.TemplateResponse(
        "contacts.html",
        {
            "request": request,
            "user": user,
            "contacts": contacts,
            "q": q,
            "status": status,
            "total_active": total_active,
            "total_inactive": total_inactive,
        },
    )


@router.post("/contacts/bulk-deactivate")
async def web_bulk_deactivate_contacts(
    request: Request,
    db: Session = Depends(get_db),
):
    user = get_current_web_user(request, db)
    form = await request.form()

    raw_ids = form.getlist("contact_ids")

    contact_ids = []
    for value in raw_ids:
        try:
            contact_ids.append(int(value))
        except ValueError:
            pass

    if contact_ids:
        db.query(Contact).filter(
            Contact.company_id == user.company_id,
            Contact.id.in_(contact_ids),
        ).update(
            {Contact.is_active: False},
            synchronize_session=False,
        )

        db.commit()

    return RedirectResponse(
        url="/web/contacts?status=all",
        status_code=303,
    )


@router.post("/contacts/bulk-restore")
async def web_bulk_restore_contacts(
    request: Request,
    db: Session = Depends(get_db),
):
    user = get_current_web_user(request, db)
    form = await request.form()

    raw_ids = form.getlist("contact_ids")

    contact_ids = []
    for value in raw_ids:
        try:
            contact_ids.append(int(value))
        except ValueError:
            pass

    if contact_ids:
        db.query(Contact).filter(
            Contact.company_id == user.company_id,
            Contact.id.in_(contact_ids),
        ).update(
            {Contact.is_active: True},
            synchronize_session=False,
        )

        db.commit()

    return RedirectResponse(
        url="/web/contacts?status=all",
        status_code=303,
    )


@router.get("/contacts/{contact_id}", response_class=HTMLResponse)
def web_contact_detail(
    contact_id: int,
    request: Request,
    db: Session = Depends(get_db),
):
    user = get_current_web_user(request, db)

    contact = db.query(Contact).filter(
        Contact.id == contact_id,
        Contact.company_id == user.company_id,
    ).first()

    if not contact:
        raise HTTPException(status_code=404, detail="Contact not found")

    company_campaigns = db.query(Campaign.id).filter(
        Campaign.company_id == user.company_id,
    ).order_by(
        Campaign.created_at.asc(),
        Campaign.id.asc(),
    ).all()

    campaign_display_map = {
        row.id: index
        for index, row in enumerate(company_campaigns)
    }

    call_rows = db.query(
        CallLog,
        Campaign,
        AudioFile,
    ).join(
        Campaign,
        CallLog.campaign_id == Campaign.id,
    ).outerjoin(
        AudioFile,
        Campaign.audio_file_id == AudioFile.id,
    ).filter(
        CallLog.contact_id == contact.id,
        Campaign.company_id == user.company_id,
    ).order_by(
        CallLog.id.desc(),
    ).all()

    history = []

    for call, campaign, audio in call_rows:
        history.append({
            "call": call,
            "campaign": campaign,
            "campaign_no": campaign_display_map.get(campaign.id, 0),
            "audio": audio,
        })

    groups = db.query(ContactGroup).join(
        ContactGroupMember,
        ContactGroup.id == ContactGroupMember.group_id,
    ).filter(
        ContactGroupMember.contact_id == contact.id,
        ContactGroup.company_id == user.company_id,
        ContactGroup.is_active == True,
    ).order_by(
        ContactGroup.created_at.asc(),
        ContactGroup.id.asc(),
    ).all()

    return templates.TemplateResponse(
        "contact_detail.html",
        {
            "request": request,
            "contact": contact,
            "history": history,
            "groups": groups,
        },
    )


@router.post("/contacts/{contact_id}/deactivate")
def web_deactivate_contact(
    contact_id: int,
    request: Request,
    db: Session = Depends(get_db),
):
    user = get_current_web_user(request, db)

    contact = db.query(Contact).filter(
        Contact.id == contact_id,
        Contact.company_id == user.company_id,
    ).first()

    if not contact:
        raise HTTPException(status_code=404, detail="Contact not found")

    contact.is_active = False
    db.commit()

    return RedirectResponse(
        url=f"/web/contacts/{contact.id}",
        status_code=303,
    )


@router.post("/contacts/{contact_id}/restore")
def web_restore_contact(
    contact_id: int,
    request: Request,
    db: Session = Depends(get_db),
):
    user = get_current_web_user(request, db)

    contact = db.query(Contact).filter(
        Contact.id == contact_id,
        Contact.company_id == user.company_id,
    ).first()

    if not contact:
        raise HTTPException(status_code=404, detail="Contact not found")

    contact.is_active = True
    db.commit()

    return RedirectResponse(
        url=f"/web/contacts/{contact.id}",
        status_code=303,
    )


@router.get("/contact-groups", response_class=HTMLResponse)
def web_contact_groups(
    request: Request,
    db: Session = Depends(get_db),
):
    user = get_current_web_user(request, db)

    rows = db.query(
        ContactGroup,
        func.count(ContactGroupMember.id).label("member_count"),
    ).outerjoin(
        ContactGroupMember,
        ContactGroup.id == ContactGroupMember.group_id,
    ).filter(
        ContactGroup.company_id == user.company_id,
        ContactGroup.is_active == True,
    ).group_by(
        ContactGroup.id,
    ).order_by(
        ContactGroup.created_at.asc(),
        ContactGroup.id.asc(),
    ).all()

    contact_groups = [
        {
            "group": group,
            "member_count": member_count,
        }
        for group, member_count in rows
    ]

    return templates.TemplateResponse(
        "contact_groups.html",
        {
            "request": request,
            "contact_groups": contact_groups,
        },
    )


@router.get("/contact-groups/new", response_class=HTMLResponse)
def web_new_contact_group_page(
    request: Request,
):
    return templates.TemplateResponse(
        "contact_group_new.html",
        {
            "request": request,
            "result": None,
        },
    )


@router.post("/contact-groups/new", response_class=HTMLResponse)
def web_create_contact_group(
    request: Request,
    group_name: str = Form(...),
    description: str = Form(""),
    bulk_numbers: str = Form(""),
    contact_file: Optional[UploadFile] = File(None),
    db: Session = Depends(get_db),
):
    user = get_current_web_user(request, db)

    group_name = group_name.strip()

    if not group_name:
        raise HTTPException(status_code=400, detail="Group name is required")

    existing_group = db.query(ContactGroup).filter(
        ContactGroup.company_id == user.company_id,
        ContactGroup.name == group_name,
    ).first()

    if existing_group and existing_group.is_active:
        raise HTTPException(status_code=400, detail="Group name already exists")

    if existing_group and not existing_group.is_active:
        group = existing_group
        group.is_active = True
        group.description = description.strip() or None

        db.query(ContactGroupMember).filter(
            ContactGroupMember.group_id == group.id,
        ).delete(synchronize_session=False)

        db.query(CampaignContactGroup).filter(
            CampaignContactGroup.group_id == group.id,
        ).delete(synchronize_session=False)

    else:
        group = ContactGroup(
            company_id=user.company_id,
            name=group_name,
            description=description.strip() or None,
            is_active=True,
        )

        db.add(group)
        db.flush()

    raw_rows = []

    if bulk_numbers.strip():
        parts = re.split(r"[\s,;]+", bulk_numbers.strip())

        for part in parts:
            if part.strip():
                raw_rows.append({
                    "phone": part.strip(),
                    "full_name": None,
                })

    if contact_file and contact_file.filename:
        filename = contact_file.filename.lower()

        if not (filename.endswith(".csv") or filename.endswith(".txt")):
            raise HTTPException(
                status_code=400,
                detail="Only CSV or TXT contact files are allowed",
            )

        try:
            content = contact_file.file.read().decode("utf-8-sig")
        except UnicodeDecodeError:
            raise HTTPException(status_code=400, detail="Contact file must be UTF-8 encoded")

        if filename.endswith(".txt"):
            for line in content.splitlines():
                if line.strip():
                    raw_rows.append({
                        "phone": line.strip(),
                        "full_name": None,
                    })

        elif filename.endswith(".csv"):
            stream = io.StringIO(content)
            dict_reader = csv.DictReader(stream)

            fieldnames = [
                name.strip().lower()
                for name in (dict_reader.fieldnames or [])
            ]

            if "phone" in fieldnames:
                for row in dict_reader:
                    normalized = {
                        (key or "").strip().lower(): (value or "").strip()
                        for key, value in row.items()
                    }

                    raw_rows.append({
                        "phone": normalized.get("phone", ""),
                        "full_name": normalized.get("full_name") or None,
                    })

            else:
                stream.seek(0)
                reader = csv.reader(stream)

                for row in reader:
                    if not row:
                        continue

                    raw_rows.append({
                        "phone": row[0].strip() if len(row) > 0 else "",
                        "full_name": row[1].strip() if len(row) > 1 and row[1].strip() else None,
                    })

    created_contacts = 0
    added_members = 0
    skipped = 0
    seen_phones = set()

    for row in raw_rows:
        phone = re.sub(r"\D", "", row.get("phone") or "")

        if len(phone) != 8 or phone in seen_phones:
            skipped += 1
            continue

        seen_phones.add(phone)

        contact = db.query(Contact).filter(
            Contact.company_id == user.company_id,
            Contact.phone == phone,
        ).first()

        if not contact:
            contact = Contact(
                company_id=user.company_id,
                phone=phone,
                full_name=row.get("full_name") or None,
                notes=None,
                is_active=True,
            )
            db.add(contact)
            db.flush()
            created_contacts += 1
        else:
            if not contact.is_active:
                contact.is_active = True

        member = ContactGroupMember(
            group_id=group.id,
            contact_id=contact.id,
        )

        db.add(member)
        added_members += 1

    if added_members == 0:
        db.rollback()
        raise HTTPException(status_code=400, detail="No valid numbers found for this group")

    db.commit()

    return RedirectResponse(
        url="/web/contact-groups",
        status_code=303,
    )


@router.post("/contact-groups/{group_id}/delete")
def web_delete_contact_group(
    group_id: int,
    request: Request,
    db: Session = Depends(get_db),
):
    user = get_current_web_user(request, db)

    group = db.query(ContactGroup).filter(
        ContactGroup.id == group_id,
        ContactGroup.company_id == user.company_id,
        ContactGroup.is_active == True,
    ).first()

    if not group:
        raise HTTPException(status_code=404, detail="Contact group not found")

    members = db.query(ContactGroupMember).filter(
        ContactGroupMember.group_id == group.id,
    ).all()

    contact_ids = [member.contact_id for member in members]

    db.query(CampaignContactGroup).filter(
        CampaignContactGroup.group_id == group.id,
    ).delete(synchronize_session=False)

    db.query(ContactGroupMember).filter(
        ContactGroupMember.group_id == group.id,
    ).delete(synchronize_session=False)

    group.is_active = False

    for contact_id in contact_ids:
        other_active_group_count = db.query(ContactGroupMember).join(
            ContactGroup,
            ContactGroup.id == ContactGroupMember.group_id,
        ).filter(
            ContactGroupMember.contact_id == contact_id,
            ContactGroup.company_id == user.company_id,
            ContactGroup.is_active == True,
        ).count()

        if other_active_group_count == 0:
            contact = db.query(Contact).filter(
                Contact.id == contact_id,
                Contact.company_id == user.company_id,
            ).first()

            if contact:
                contact.is_active = False

    db.commit()

    return RedirectResponse(
        url="/web/contact-groups",
        status_code=303,
    )


@router.get("/contact-groups/{group_id}", response_class=HTMLResponse)
def web_contact_group_detail(
    group_id: int,
    request: Request,
    db: Session = Depends(get_db),
):
    user = get_current_web_user(request, db)

    group = db.query(ContactGroup).filter(
        ContactGroup.id == group_id,
        ContactGroup.company_id == user.company_id,
        ContactGroup.is_active == True,
    ).first()

    if not group:
        raise HTTPException(status_code=404, detail="Contact group not found")

    company_groups = db.query(ContactGroup.id).filter(
        ContactGroup.company_id == user.company_id,
        ContactGroup.is_active == True,
    ).order_by(
        ContactGroup.created_at.asc(),
        ContactGroup.id.asc(),
    ).all()

    group_display_map = {
        row.id: index
        for index, row in enumerate(company_groups)
    }

    group_display_id = group_display_map.get(group.id, 0)

    members = db.query(Contact).join(
        ContactGroupMember,
        Contact.id == ContactGroupMember.contact_id,
    ).filter(
        ContactGroupMember.group_id == group.id,
        Contact.company_id == user.company_id,
        Contact.is_active == True,
    ).order_by(
        Contact.id.asc(),
    ).all()

    member_ids = [member.id for member in members]

    campaign_links = db.query(CampaignContactGroup).filter(
        CampaignContactGroup.group_id == group.id,
    ).all()

    campaign_ids = [link.campaign_id for link in campaign_links]

    campaigns = []

    if campaign_ids:
        campaign_list = db.query(Campaign).filter(
            Campaign.id.in_(campaign_ids),
            Campaign.company_id == user.company_id,
        ).order_by(
            Campaign.created_at.asc(),
            Campaign.id.asc(),
        ).all()

        for campaign in campaign_list:
            counts = {}

            if member_ids:
                counts = dict(
                    db.query(CallLog.status, func.count(CallLog.id))
                    .filter(
                        CallLog.campaign_id == campaign.id,
                        CallLog.contact_id.in_(member_ids),
                    )
                    .group_by(CallLog.status)
                    .all()
                )

            completed = counts.get(CallStatus.completed, 0)
            failed = counts.get(CallStatus.failed, 0)
            busy = counts.get(CallStatus.busy, 0)
            no_answer = counts.get(CallStatus.no_answer, 0)
            congestion = counts.get(CallStatus.congestion, 0)
            calling = counts.get(CallStatus.calling, 0)

            finished = completed + failed + busy + no_answer + congestion
            total = len(member_ids)

            progress_percent = round((finished / total) * 100, 2) if total else 0

            campaigns.append({
                "campaign": campaign,
                "completed": completed,
                "failed": failed,
                "busy": busy,
                "no_answer": no_answer,
                "congestion": congestion,
                "calling": calling,
                "finished": finished,
                "total": total,
                "progress_percent": progress_percent,
            })

    return templates.TemplateResponse(
        "contact_group_detail.html",
        {
            "request": request,
            "group": group,
            "group_display_id": group_display_id,
            "members": members,
            "member_count": len(members),
            "campaigns": campaigns,
        },
    )


@router.get("/audio/upload", response_class=HTMLResponse)
def web_upload_audio_page(
    request: Request,
    saved: Optional[int] = Query(None),
    msg: Optional[str] = Query(None),
    db: Session = Depends(get_db),
):
    user = get_current_web_user(request, db)

    audio_files = active_audio_files(db, user.company_id)

    result = None

    if saved:
        saved_audio = next(
            (item for item in audio_files if item.id == saved),
            None,
        )

        if saved_audio:
            result = audio_result_payload(db, user.company_id, saved_audio)

    return templates.TemplateResponse(
        "audio_upload.html",
        {
            "request": request,
            "user": user,
            "audio_files": audio_files,
            "result": result,
            "notice": AUDIO_PAGE_NOTICES.get(msg or ""),
            "error": None,
        },
    )


@router.post("/audio/upload", response_class=HTMLResponse)
def web_upload_audio(
    request: Request,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    user = get_current_web_user(request, db)

    def render(error: str | None, result: dict | None = None):
        return templates.TemplateResponse(
            "audio_upload.html",
            {
                "request": request,
                "user": user,
                "audio_files": active_audio_files(db, user.company_id),
                "result": result,
                "notice": None,
                "error": error,
            },
        )

    ext = Path(file.filename or "").suffix.lower()

    if ext not in ALLOWED_EXTENSIONS:
        return render("Only mp3, wav, m4a, ogg, flac, gsm files are allowed.")

    os.makedirs(ASTERISK_SOUNDS_DIR, exist_ok=True)

    safe_name = safe_audio_name(file.filename or "audio")
    unique_name = f"{safe_name}_{int(time.time())}"

    temp_input_path = f"/tmp/{unique_name}{ext}"
    output_filename = f"{unique_name}.wav"
    output_path = os.path.join(ASTERISK_SOUNDS_DIR, output_filename)

    try:
        with open(temp_input_path, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)

        try:
            AudioConverter.convert_to_wav_8k_mono(
                input_path=temp_input_path,
                output_path=output_path,
            )
        except RuntimeError as exc:
            if "Permission denied" in str(exc):
                raise RuntimeError(
                    "The server could not save the converted audio file because of a "
                    "permissions problem on the Asterisk sounds directory. "
                    "Please contact your administrator to fix folder permissions."
                ) from exc
            raise

        audio = register_audio_file(
            db=db,
            company_id=user.company_id,
            unique_name=unique_name,
            output_path=output_path,
            source=AudioSource.upload,
        )

        return render(None, audio_result_payload(db, user.company_id, audio))

    except HTTPException as exc:
        db.rollback()
        safe_remove_file(output_path)
        return render(str(exc.detail))

    except Exception as exc:
        db.rollback()
        safe_remove_file(output_path)
        return render(f"Audio upload failed: {exc}")

    finally:
        safe_remove_file(temp_input_path)


@router.post("/audio/{audio_id}/delete")
def web_delete_audio(
    audio_id: int,
    request: Request,
    db: Session = Depends(get_db),
):
    user = get_current_web_user(request, db)

    audio = get_company_audio(db, user.company_id, audio_id)

    audio.is_active = False
    db.commit()

    return RedirectResponse(
        url="/web/audio/upload",
        status_code=303,
    )


@router.get("/audio/{audio_id}/stream")
def web_stream_audio(
    audio_id: int,
    request: Request,
    db: Session = Depends(get_db),
):
    """Serve one company's audio back to the browser so it can be played.

    Inactive audio is served too: a deleted file stays attached to the reports
    of campaigns that already used it, and those still need to be listenable.
    """
    user = get_current_web_user(request, db)

    audio = get_company_audio(db, user.company_id, audio_id, active_only=False)

    audio_path = Path(audio.file_path)

    if not audio_path.exists():
        raise HTTPException(
            status_code=404,
            detail="The audio file is missing from the server's sounds folder.",
        )

    return FileResponse(
        path=str(audio_path),
        media_type="audio/wav",
        filename=f"{audio.label}.wav",
        content_disposition_type="inline",
    )


@router.post("/audio/record")
async def web_record_audio(
    request: Request,
    file: UploadFile = File(...),
    name: str = Form(""),
    db: Session = Depends(get_db),
):
    """Store a microphone recording made in the browser.

    The browser posts whatever container MediaRecorder gave it (webm/ogg/mp4);
    ffmpeg turns that into the same 8kHz mono WAV an upload produces, so a
    recording is interchangeable with an uploaded file everywhere else.
    """
    user = get_current_web_user(request, db)

    ext = Path(file.filename or "").suffix.lower()

    if ext not in RECORDING_EXTENSIONS:
        ext = RECORDING_MIME_EXTENSIONS.get(
            (file.content_type or "").split(";")[0].strip().lower(),
            "",
        )

    if not ext:
        return JSONResponse(
            status_code=400,
            content={"detail": "That recording format is not supported by this browser."},
        )

    os.makedirs(ASTERISK_SOUNDS_DIR, exist_ok=True)

    display_name = (name or "").strip()[:200]
    safe_name = safe_audio_name(display_name or "recording")
    unique_name = f"rec_{safe_name}_{int(time.time())}"

    temp_input_path = os.path.join(settings.AUDIO_TEMP_DIR, f"{unique_name}{ext}")
    output_path = os.path.join(ASTERISK_SOUNDS_DIR, f"{unique_name}.wav")

    try:
        recording_bytes = await file.read()

        if not recording_bytes:
            return JSONResponse(
                status_code=400,
                content={"detail": "The recording was empty. Please record again."},
            )

        with open(temp_input_path, "wb") as buffer:
            buffer.write(recording_bytes)

        try:
            AudioConverter.convert_to_wav_8k_mono(
                input_path=temp_input_path,
                output_path=output_path,
            )
        except RuntimeError as exc:
            if "Permission denied" in str(exc):
                raise RuntimeError(
                    "The server could not save the recording because of a "
                    "permissions problem on the Asterisk sounds directory. "
                    "Please contact your administrator to fix folder permissions."
                ) from exc
            raise

        audio = register_audio_file(
            db=db,
            company_id=user.company_id,
            unique_name=unique_name,
            output_path=output_path,
            source=AudioSource.record,
            display_name=display_name,
        )

        return JSONResponse(
            status_code=200,
            content={
                "id": audio.id,
                "redirect": f"/web/audio/upload?saved={audio.id}",
            },
        )

    except HTTPException as exc:
        db.rollback()
        safe_remove_file(output_path)
        return JSONResponse(status_code=exc.status_code, content={"detail": str(exc.detail)})

    except Exception as exc:
        db.rollback()
        safe_remove_file(output_path)
        return JSONResponse(
            status_code=500,
            content={"detail": f"Saving the recording failed: {exc}"},
        )

    finally:
        safe_remove_file(temp_input_path)


@router.post("/audio/{audio_id}/rename")
def web_rename_audio(
    audio_id: int,
    request: Request,
    display_name: str = Form(""),
    db: Session = Depends(get_db),
):
    """Rename the label only.

    `filename` is what Asterisk plays back and what queued campaigns already
    point at, so it is deliberately left alone.
    """
    user = get_current_web_user(request, db)

    audio = get_company_audio(db, user.company_id, audio_id)

    new_name = (display_name or "").strip()

    if len(new_name) > 200:
        raise HTTPException(
            status_code=400,
            detail="Audio name is too long. Please keep it under 200 characters.",
        )

    # Clearing the field falls back to the stored filename.
    audio.display_name = new_name or None
    db.commit()

    return RedirectResponse(
        url="/web/audio/upload?msg=renamed",
        status_code=303,
    )


@router.post("/audio/{audio_id}/trim")
def web_trim_audio(
    audio_id: int,
    request: Request,
    start_sec: float = Form(0.0),
    end_sec: float = Form(...),
    replace_original: str = Form(""),
    db: Session = Depends(get_db),
):
    """Cut a section out of an audio file and save it as a new library entry.

    Trimming never edits the original in place: campaigns that already ran with
    it, and their reports, have to keep pointing at the audio that was actually
    played. When the original is unused and the user asked to replace it, it is
    deactivated afterwards so the library does not fill up with near-duplicates.
    """
    user = get_current_web_user(request, db)

    audio = get_company_audio(db, user.company_id, audio_id)

    start = max(0.0, float(start_sec or 0.0))
    end = float(end_sec or 0.0)
    duration = float(audio.duration_sec or 0.0)

    if duration and end > duration:
        end = duration

    if end - start < MIN_TRIM_DURATION_SEC:
        raise HTTPException(
            status_code=400,
            detail=(
                f"The selected section is too short. "
                f"Please keep at least {MIN_TRIM_DURATION_SEC} seconds."
            ),
        )

    if not Path(audio.file_path).exists():
        raise HTTPException(
            status_code=404,
            detail="The audio file is missing from the server's sounds folder.",
        )

    os.makedirs(ASTERISK_SOUNDS_DIR, exist_ok=True)

    # Room for the "_trim_<timestamp>" suffix inside filename's 200 chars.
    base_name = safe_audio_name(audio.filename)[:150]
    unique_name = f"{base_name}_trim_{int(time.time())}"
    output_path = os.path.join(ASTERISK_SOUNDS_DIR, f"{unique_name}.wav")

    try:
        AudioConverter.trim_to_wav_8k_mono(
            input_path=audio.file_path,
            output_path=output_path,
            start_sec=start,
            end_sec=end,
        )

        trimmed = register_audio_file(
            db=db,
            company_id=user.company_id,
            unique_name=unique_name,
            output_path=output_path,
            source=audio.source,
            tts_text=audio.tts_text,
            display_name=f"{audio.label} (trimmed)"[:200],
        )

    except HTTPException:
        db.rollback()
        safe_remove_file(output_path)
        raise

    except Exception as exc:
        db.rollback()
        safe_remove_file(output_path)
        raise HTTPException(status_code=400, detail=f"Trimming failed: {exc}")

    message = "trimmed_new"

    if replace_original:
        used_by_campaign = db.query(Campaign).filter(
            Campaign.audio_file_id == audio.id,
        ).count()

        if used_by_campaign:
            message = "trimmed_kept"
        else:
            audio.is_active = False
            db.commit()
            message = "trimmed"

    return RedirectResponse(
        url=f"/web/audio/upload?saved={trimmed.id}&msg={message}",
        status_code=303,
    )

@router.post("/audio/tts", response_class=HTMLResponse)
def web_generate_tts_audio(
    request: Request,
    text: str = Form(...),
    voice: str = Form(DEFAULT_TTS_VOICE),
    db: Session = Depends(get_db),
):
    user = get_current_web_user(request, db)

    def render(error: str | None, result: dict | None = None):
        return templates.TemplateResponse(
            "audio_upload.html",
            {
                "request": request,
                "user": user,
                "audio_files": active_audio_files(db, user.company_id),
                "result": result,
                "notice": None,
                "error": error,
            },
        )

    text = (text or "").strip()

    if not text:
        return render("Please enter some text to generate audio from.")

    os.makedirs(ASTERISK_SOUNDS_DIR, exist_ok=True)

    safe_name = safe_audio_name(text[:40] or "tts")
    unique_name = f"tts_{safe_name}_{int(time.time())}"

    temp_tts_path = f"/tmp/{unique_name}.mp3"
    output_filename = f"{unique_name}.wav"
    output_path = os.path.join(ASTERISK_SOUNDS_DIR, output_filename)

    try:
        TTSService.generate_speech(
            text=text,
            output_path=temp_tts_path,
            voice=voice or DEFAULT_TTS_VOICE,
        )

        AudioConverter.convert_to_wav_8k_mono(
            input_path=temp_tts_path,
            output_path=output_path,
        )

        audio = register_audio_file(
            db=db,
            company_id=user.company_id,
            unique_name=unique_name,
            output_path=output_path,
            source=AudioSource.tts,
            tts_text=text,
        )

        return render(None, audio_result_payload(db, user.company_id, audio))

    except HTTPException as exc:
        db.rollback()
        safe_remove_file(output_path)
        return render(str(exc.detail))

    except Exception as exc:
        db.rollback()
        safe_remove_file(output_path)
        return render(f"Text-to-speech generation failed: {exc}")

    finally:
        safe_remove_file(temp_tts_path)

@router.get("/campaigns/{campaign_id}/contacts", response_class=HTMLResponse)
def web_campaign_contacts(
    campaign_id: int,
    request: Request,
    page: int = Query(1, ge=1),
    db: Session = Depends(get_db),
):
    user = get_current_web_user(request, db)

    campaign = db.query(Campaign).filter(
        Campaign.id == campaign_id,
        Campaign.company_id == user.company_id,
    ).first()

    if not campaign:
        raise HTTPException(status_code=404, detail="Campaign not found")

    call_count = db.query(CallLog).filter(
        CallLog.campaign_id == campaign.id,
    ).count()

    can_edit = (
        campaign.status == CampaignStatus.draft
        and call_count == 0
    )

    per_page = 100

    target_query = db.query(CampaignTarget).filter(
        CampaignTarget.campaign_id == campaign.id,
    ).order_by(
        CampaignTarget.position.asc(),
        CampaignTarget.id.asc(),
    )

    total_campaign_contacts = target_query.count()

    use_old_target_ids = False

    if total_campaign_contacts == 0:
        use_old_target_ids = True
        target_ids = campaign.target_contact_ids or []
        total_campaign_contacts = len(target_ids)

    total_pages = max(1, (total_campaign_contacts + per_page - 1) // per_page)

    if page > total_pages:
        page = total_pages

    offset = (page - 1) * per_page

    contacts = []

    if not use_old_target_ids:
        target_rows = target_query.offset(offset).limit(per_page).all()

        contact_ids = [
            row.contact_id
            for row in target_rows
            if row.contact_id is not None
        ]

        contact_rows = []

        if contact_ids:
            contact_rows = db.query(Contact).filter(
                Contact.company_id == user.company_id,
                Contact.id.in_(contact_ids),
            ).all()

        contact_map = {
            contact.id: contact
            for contact in contact_rows
        }

        for target in target_rows:
            contact = contact_map.get(target.contact_id)

            if contact:
                contact.target_status = target.status
                contact.target_attempts = target.attempts
                contact.target_call_log_id = target.call_log_id
                contact.target_position = target.position
                contacts.append(contact)

    else:
        target_ids = campaign.target_contact_ids or []
        page_target_ids = target_ids[offset:offset + per_page]

        if page_target_ids:
            contact_rows = db.query(Contact).filter(
                Contact.company_id == user.company_id,
                Contact.id.in_(page_target_ids),
            ).all()

            contact_map = {
                contact.id: contact
                for contact in contact_rows
            }

            contacts = [
                contact_map[contact_id]
                for contact_id in page_target_ids
                if contact_id in contact_map
            ]

    campaign_display_id = get_company_campaign_display_id(campaign, db)

    return templates.TemplateResponse(
        "campaign_contacts.html",
        {
            "request": request,
            "user": user,
            "campaign": campaign,
            "campaign_display_id": campaign_display_id,
            "contacts": contacts,
            "page": page,
            "per_page": per_page,
            "total_pages": total_pages,
            "total_campaign_contacts": total_campaign_contacts,
            "can_edit": can_edit,
            "call_count": call_count,
        },
    )


@router.post("/campaigns/{campaign_id}/contacts/remove")
async def web_remove_campaign_contacts(
    campaign_id: int,
    request: Request,
    db: Session = Depends(get_db),
):
    user = get_current_web_user(request, db)

    campaign = db.query(Campaign).filter(
        Campaign.id == campaign_id,
        Campaign.company_id == user.company_id,
    ).first()

    if not campaign:
        raise HTTPException(status_code=404, detail="Campaign not found")

    call_count = db.query(CallLog).filter(
        CallLog.campaign_id == campaign.id,
    ).count()

    if campaign.status != CampaignStatus.draft or call_count > 0:
        raise HTTPException(
            status_code=400,
            detail="This campaign was already executed. Numbers cannot be removed.",
        )

    form = await request.form()
    raw_ids = form.getlist("contact_ids")

    remove_ids = set()

    for value in raw_ids:
        try:
            remove_ids.add(int(value))
        except ValueError:
            pass

    old_target_ids = campaign.target_contact_ids or []

    new_target_ids = [
        contact_id
        for contact_id in old_target_ids
        if int(contact_id) not in remove_ids
    ]

    campaign.target_contact_ids = new_target_ids

    deleted_targets = db.query(CampaignTarget).filter(
        CampaignTarget.campaign_id == campaign.id,
        CampaignTarget.contact_id.in_(list(remove_ids)),
    ).delete(
        synchronize_session=False,
    )

    remaining_target_count = db.query(CampaignTarget).filter(
        CampaignTarget.campaign_id == campaign.id,
    ).count()

    campaign.total_contacts = remaining_target_count or len(new_target_ids)

    db.commit()

    print(
        f"WEB REMOVE TARGETS: campaign_id={campaign.id}, "
        f"removed_contact_ids={sorted(remove_ids)}, "
        f"deleted_campaign_targets={deleted_targets}, "
        f"remaining_total={campaign.total_contacts}"
    )

    return RedirectResponse(
        url=f"/web/campaigns/{campaign.id}/contacts",
        status_code=303,
    )


@router.get("/campaigns/not-executed", response_class=HTMLResponse)
def web_not_executed_campaigns(
    request: Request,
    db: Session = Depends(get_db),
):
    user = get_current_web_user(request, db)

    campaigns = db.query(Campaign).filter(
        Campaign.company_id == user.company_id,
        Campaign.status == CampaignStatus.draft,
    ).order_by(
        Campaign.created_at.asc(),
        Campaign.id.asc(),
    ).all()

    rows = []

    for index, campaign in enumerate(campaigns):
        call_count = db.query(CallLog).filter(
            CallLog.campaign_id == campaign.id,
        ).count()

        if call_count == 0:
            rows.append({
                "display_id": index,
                "campaign": campaign,
                "call_count": call_count,
            })

    return templates.TemplateResponse(
        "campaigns_not_executed.html",
        {
            "request": request,
            "rows": rows,
        },
    )


@router.post("/campaigns/{campaign_id}/delete-not-executed")
def web_delete_not_executed_campaign(
    campaign_id: int,
    request: Request,
    db: Session = Depends(get_db),
):
    user = get_current_web_user(request, db)

    campaign = db.query(Campaign).filter(
        Campaign.id == campaign_id,
        Campaign.company_id == user.company_id,
    ).first()

    if not campaign:
        raise HTTPException(status_code=404, detail="Campaign not found")

    call_count = db.query(CallLog).filter(
        CallLog.campaign_id == campaign.id,
    ).count()

    if campaign.status != CampaignStatus.draft or call_count > 0:
        raise HTTPException(
            status_code=400,
            detail="Only not-executed draft campaigns can be deleted.",
        )

    db.query(CampaignContactGroup).filter(
        CampaignContactGroup.campaign_id == campaign.id,
    ).delete(synchronize_session=False)

    db.delete(campaign)
    db.commit()

    return RedirectResponse(
        url="/web/campaigns/not-executed",
        status_code=303,
    )


@router.get("/campaigns/{campaign_id}", response_class=HTMLResponse)
def web_campaign_detail(
    campaign_id: int,
    request: Request,
    page: int = Query(1, ge=1),
    db: Session = Depends(get_db),
):
    user = get_current_web_user(request, db)

    campaign = db.query(Campaign).filter(
        Campaign.id == campaign_id,
        Campaign.company_id == user.company_id,
    ).first()

    if not campaign:
        raise HTTPException(status_code=404, detail="Campaign not found")

    campaign_display_id = get_company_campaign_display_id(campaign, db)

    per_page = 100

    total_call_logs = db.query(func.count(CallLog.id)).filter(
        CallLog.campaign_id == campaign.id,
    ).scalar() or 0

    total_pages = max(1, (total_call_logs + per_page - 1) // per_page)

    if page > total_pages:
        page = total_pages

    offset = (page - 1) * per_page

    calls = db.query(CallLog).filter(
        CallLog.campaign_id == campaign.id,
    ).order_by(
        CallLog.id.desc(),
    ).offset(offset).limit(per_page).all()

    campaign_group_rows = []

    group_links = db.query(CampaignContactGroup).filter(
        CampaignContactGroup.campaign_id == campaign.id,
    ).all()

    target_ids = campaign.target_contact_ids or []

    for link in group_links:
        group = db.query(ContactGroup).filter(
            ContactGroup.id == link.group_id,
            ContactGroup.company_id == campaign.company_id,
        ).first()

        if not group:
            continue

        members = db.query(Contact).join(
            ContactGroupMember,
            Contact.id == ContactGroupMember.contact_id,
        ).filter(
            ContactGroupMember.group_id == group.id,
            Contact.company_id == campaign.company_id,
            Contact.id.in_(target_ids),
        ).all()

        member_ids = [member.id for member in members]

        counts = {}

        if member_ids:
            raw_counts = db.query(
                CallLog.status,
                func.count(CallLog.id),
            ).filter(
                CallLog.campaign_id == campaign.id,
                CallLog.contact_id.in_(member_ids),
            ).group_by(
                CallLog.status,
            ).all()

            counts = dict(raw_counts)

        def status_count(status_enum):
            return counts.get(status_enum, 0) or counts.get(status_enum.value, 0)

        completed = status_count(CallStatus.completed)
        failed = status_count(CallStatus.failed)
        busy = status_count(CallStatus.busy)
        no_answer = status_count(CallStatus.no_answer)
        congestion = status_count(CallStatus.congestion)
        calling = status_count(CallStatus.calling)

        finished = completed + failed + busy + no_answer + congestion
        total = len(member_ids)

        progress_percent = round((finished / total) * 100, 2) if total else 0

        campaign_group_rows.append({
            "group": group,
            "total": total,
            "completed": completed,
            "failed": failed,
            "busy": busy,
            "no_answer": no_answer,
            "congestion": congestion,
            "calling": calling,
            "finished": finished,
            "progress_percent": progress_percent,
        })

    sip_rows = usable_sip_rows_for_company(db, user.company_id)
    has_available_sip = bool(sip_rows)

    start_disabled_reason = campaign_start_disabled_reason(
        campaign,
        sip_rows,
        db,
    )

    target_status_rows = db.query(CampaignTarget).filter(
        CampaignTarget.campaign_id == campaign.id,
    ).order_by(
        CampaignTarget.position.asc(),
        CampaignTarget.id.asc(),
    ).limit(300).all()

    target_status_total = db.query(func.count(CampaignTarget.id)).filter(
        CampaignTarget.campaign_id == campaign.id,
    ).scalar() or 0

    cancelled_result_rows = db.query(CampaignTarget).filter(
        CampaignTarget.campaign_id == campaign.id,
        CampaignTarget.status == "cancelled",
        CampaignTarget.call_log_id == None,
    ).order_by(
        CampaignTarget.position.asc(),
        CampaignTarget.id.asc(),
    ).all()

    cancelled_result_total = len(cancelled_result_rows)

    return templates.TemplateResponse(
        "campaign_detail.html",
        {
            "request": request,
            "campaign": campaign,
            "campaign_display_id": campaign_display_id,
            "calls": calls,
            "page": page,
            "per_page": per_page,
            "total_pages": total_pages,
            "total_call_logs": total_call_logs,
            "campaign_groups": campaign_group_rows,
            "sip_rows": sip_rows,
            "has_available_sip": has_available_sip,
            "start_disabled_reason": start_disabled_reason,
            "target_status_rows": target_status_rows,
            "target_status_total": target_status_total,
            "cancelled_result_rows": cancelled_result_rows,
            "cancelled_result_total": cancelled_result_total,
        },
    )


@router.get("/campaigns/{campaign_id}/dry-run", response_class=HTMLResponse)
def web_campaign_dry_run(
    campaign_id: int,
    request: Request,
    db: Session = Depends(get_db),
):
    return RedirectResponse(
        url=f"/web/campaigns/{campaign_id}",
        status_code=303,
    )


@router.get("/campaigns/{campaign_id}/report.csv")
def web_export_campaign_report(
    campaign_id: int,
    request: Request,
    db: Session = Depends(get_db),
):
    user = get_current_web_user(request, db)

    campaign = db.query(Campaign).filter(
        Campaign.id == campaign_id,
        Campaign.company_id == user.company_id,
    ).first()

    if not campaign:
        raise HTTPException(status_code=404, detail="Campaign not found")

    filename = f"campaign_{campaign.id}_report.csv"

    def generate_csv():
        output = io.StringIO()
        writer = csv.writer(output)

        writer.writerow([
            "phone",
            "status",
            "duration_sec",
            "hangup_cause",
            "started_at",
            "answered_at",
            "ended_at",
        ])

        yield output.getvalue()
        output.seek(0)
        output.truncate(0)

        query = db.query(CallLog).filter(
            CallLog.campaign_id == campaign.id,
        ).order_by(
            CallLog.started_at.asc(),
            CallLog.id.asc(),
        ).yield_per(500)

        for call in query:
            writer.writerow([
                call.phone,
                call.status.value if call.status else "",
                call.duration_sec if call.duration_sec is not None else "",
                call.hangup_cause if call.hangup_cause is not None else "",
                call.started_at.isoformat() if call.started_at else "",
                call.answered_at.isoformat() if call.answered_at else "",
                call.ended_at.isoformat() if call.ended_at else "",
            ])

            yield output.getvalue()
            output.seek(0)
            output.truncate(0)

    return StreamingResponse(
        generate_csv(),
        media_type="text/csv",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
        },
    )


@router.post("/profile/tokens/buy", response_class=HTMLResponse)
async def web_buy_tokens(
    request: Request,
    db: Session = Depends(get_db),
):
    """Create an unpaid token order and a QPay invoice to pay it with.

    Tokens are NOT credited here. The buyer is sent to the QR page, and the
    balance only moves once QPay confirms the payment through
    settle_qpay_purchase.
    """
    user = get_current_web_user(request, db)

    form = await request.form()
    package_code = str(form.get("package_code") or "").strip()

    raw_count = str(form.get("call_count") or "").strip()
    call_count = int(raw_count) if raw_count.isdigit() else None

    try:
        purchase = billing_service.create_purchase(
            db=db,
            company_id=user.company_id,
            package_code=package_code,
            call_count=call_count,
            user_id=user.id,
            payment_provider="qpay",
        )

        # flush() in create_purchase gives the row its id, which the invoice
        # needs for both sender_invoice_no and the callback URL.
        invoice = qpay_service.create_invoice(
            purchase_id=purchase.id,
            amount_mnt=int(purchase.amount_mnt),
            description=f"Voicebro {purchase.call_count} call tokens",
        )

        purchase.provider_ref = str(invoice["invoice_id"])
        purchase.provider_payload = qpay_service.payload_for_storage(invoice)

        db.commit()

    except HTTPException as exc:
        db.rollback()
        return render_profile(request, user, db, error=str(exc.detail), status_code=400)

    except qpay_service.QPayError as exc:
        db.rollback()
        return render_profile(
            request,
            user,
            db,
            error=f"Could not start the payment: {exc}",
        )

    except Exception as exc:
        db.rollback()
        return render_profile(request, user, db, error=f"Purchase failed: {exc}")

    return RedirectResponse(
        url=f"/web/payments/{purchase.id}",
        status_code=303,
    )


def get_company_purchase(db: Session, company_id: int, purchase_id: int) -> TokenPurchase:
    purchase = db.query(TokenPurchase).filter(
        TokenPurchase.id == purchase_id,
        TokenPurchase.company_id == company_id,
    ).first()

    if not purchase:
        raise HTTPException(status_code=404, detail="Purchase not found")

    return purchase


@router.get("/payments/{purchase_id}", response_class=HTMLResponse)
def web_payment_page(
    purchase_id: int,
    request: Request,
    db: Session = Depends(get_db),
):
    """Show the QPay QR and bank links for one order."""
    user = get_current_web_user(request, db)

    purchase = get_company_purchase(db, user.company_id, purchase_id)

    invoice = {}

    if purchase.provider_payload:
        try:
            invoice = json.loads(purchase.provider_payload)
        except ValueError:
            invoice = {}

    # qr_image is deliberately not stored on the row, so the page draws the QR
    # from qr_text instead of a 10KB base64 blob per order.
    return templates.TemplateResponse(
        "payment_qpay.html",
        {
            "request": request,
            "user": user,
            "purchase": purchase,
            "is_paid": purchase.status == PurchaseStatus.paid,
            "qr_text": invoice.get("qr_text") or "",
            "short_url": invoice.get("qPay_shortUrl") or "",
            "bank_urls": invoice.get("urls") or [],
        },
    )


@router.get("/payments/{purchase_id}/qr.svg")
def web_payment_qr(
    purchase_id: int,
    request: Request,
    db: Session = Depends(get_db),
):
    """Draw the QPay QR from the stored qr_text.

    QPay also returns a ready-made PNG, but it is a ~10KB base64 blob that
    would sit on every purchase row forever. qr_text is 252 characters and
    encodes exactly the same thing, so the image is drawn on demand instead.
    """
    user = get_current_web_user(request, db)

    purchase = get_company_purchase(db, user.company_id, purchase_id)

    qr_text = ""

    if purchase.provider_payload:
        try:
            qr_text = json.loads(purchase.provider_payload).get("qr_text") or ""
        except ValueError:
            qr_text = ""

    if not qr_text:
        raise HTTPException(status_code=404, detail="This order has no payment QR.")

    qr = qrcode.QRCode(
        box_size=10,
        border=2,
        error_correction=qrcode.constants.ERROR_CORRECT_M,
    )
    qr.add_data(qr_text)
    qr.make(fit=True)

    buffer = io.BytesIO()
    qr.make_image(image_factory=qrcode.image.svg.SvgPathImage).save(buffer)

    return Response(
        content=buffer.getvalue(),
        media_type="image/svg+xml",
        headers={"Cache-Control": "private, max-age=600"},
    )


@router.get("/payments/{purchase_id}/status")
def web_payment_status(
    purchase_id: int,
    request: Request,
    db: Session = Depends(get_db),
):
    """Poll target for the payment page.

    The buyer pays in a banking app, so nothing tells this browser tab that the
    money arrived. QPay's callback usually lands first and this just reports
    it; when the callback is late or lost, the check here settles the order
    instead, so a paying customer is never left stuck on the QR.
    """
    user = get_current_web_user(request, db)

    purchase = get_company_purchase(db, user.company_id, purchase_id)

    if purchase.status == PurchaseStatus.paid:
        return JSONResponse({"status": "paid", "call_count": purchase.call_count})

    try:
        result = payment_routes.settle_qpay_purchase(db, purchase)

    except qpay_service.QPayError as exc:
        db.rollback()
        return JSONResponse({"status": "pending", "detail": str(exc)})

    except Exception as exc:
        db.rollback()
        return JSONResponse({"status": "pending", "detail": f"Check failed: {exc}"})

    return JSONResponse({
        "status": result["status"],
        "call_count": purchase.call_count,
    })


@router.post("/payments/{purchase_id}/cancel")
def web_cancel_payment(
    purchase_id: int,
    request: Request,
    db: Session = Depends(get_db),
):
    user = get_current_web_user(request, db)

    purchase = get_company_purchase(db, user.company_id, purchase_id)

    if purchase.status == PurchaseStatus.pending:
        purchase.status = PurchaseStatus.cancelled
        db.commit()

    return RedirectResponse(url="/web/profile", status_code=303)


@router.get("/stt", response_class=HTMLResponse)
def web_stt_page(
    request: Request,
    db: Session = Depends(get_db),
):
    user = get_current_web_user(request, db)

    return templates.TemplateResponse(
        "stt.html",
        {
            "request": request,
            "user": user,
        },
    )


@router.post("/stt/transcribe")
async def web_stt_transcribe(
    request: Request,
    db: Session = Depends(get_db),
):
    """Proxy a transcription request to the STT service.

    The browser never sees the STT service's API key: this route attaches it
    server-side. Being logged into Voicebro is what authorizes the call
    instead. The STT service itself still enforces its key for anyone calling
    it directly (its public URL), so this does not weaken that.
    """
    get_current_web_user(request, db)

    form = await request.form()
    upload = form.get("file")

    if upload is None or not hasattr(upload, "filename"):
        raise HTTPException(status_code=400, detail="No audio file provided.")

    file_bytes = await upload.read()

    if not file_bytes:
        raise HTTPException(status_code=400, detail="Uploaded file is empty.")

    try:
        response = requests.post(
            f"{settings.STT_INTERNAL_URL}/transcribe",
            headers={"X-API-Key": settings.STT_API_KEY},
            files={"file": (upload.filename, file_bytes, upload.content_type)},
            timeout=900,
        )

    except requests.exceptions.RequestException as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Could not reach the STT service: {exc}",
        )

    try:
        payload = response.json()
    except ValueError:
        payload = {"detail": response.text[:500] or "STT service returned an unreadable response."}

    return JSONResponse(
        status_code=response.status_code,
        content=payload,
    )


@router.post("/campaigns/{campaign_id}/simulate")
def web_campaign_simulate(
    campaign_id: int,
    request: Request,
    db: Session = Depends(get_db),
):
    return RedirectResponse(
        url=f"/web/campaigns/{campaign_id}",
        status_code=303,
    )


@router.post("/campaigns/{campaign_id}/cancel")
def web_cancel_campaign(
    campaign_id: int,
    request: Request,
    db: Session = Depends(get_db),
):
    user = get_current_web_user(request, db)

    campaign = db.query(Campaign).filter(
        Campaign.id == campaign_id,
        Campaign.company_id == user.company_id,
    ).first()

    if not campaign:
        raise HTTPException(status_code=404, detail="Campaign not found")

    status_value = campaign_status_value(campaign)

    if status_value not in ["running", "queued"]:
        raise HTTPException(
            status_code=400,
            detail="Only running or queued campaigns can be cancelled.",
        )

    now = datetime.now(timezone.utc)

    campaign.status = CampaignStatus.cancelled
    campaign.completed_at = now

    cancelled_targets = db.query(CampaignTarget).filter(
        CampaignTarget.campaign_id == campaign.id,
        CampaignTarget.status.in_(["pending"]),
    ).update(
        {
            CampaignTarget.status: "cancelled",
            CampaignTarget.updated_at: now,
        },
        synchronize_session=False,
    )

    db.commit()

    print(
        f"WEB CANCEL CAMPAIGN: campaign_id={campaign.id}, "
        f"cancelled_targets={cancelled_targets}"
    )

    return RedirectResponse(
        url=f"/web/campaigns/{campaign.id}",
        status_code=303,
    )


@router.post("/campaigns/{campaign_id}/real-start")
def web_real_start_campaign(
    campaign_id: int,
    request: Request,
    sip_trunk_id: Optional[int] = Form(None),
    db: Session = Depends(get_db),
):
    user = get_current_web_user(request, db)

    campaign = db.query(Campaign).filter(
        Campaign.id == campaign_id,
        Campaign.company_id == user.company_id,
    ).first()

    if not campaign:
        raise HTTPException(status_code=404, detail="Campaign not found")

    print(
        f"WEB REAL START DEBUG: campaign_id={campaign.id}, "
        f"received_sip_trunk_id={sip_trunk_id}"
    )

    sip_rows = usable_sip_rows_for_company(db, user.company_id)

    disabled_reason = campaign_start_disabled_reason(
        campaign,
        sip_rows,
        db,
    )

    if disabled_reason:
        raise HTTPException(
            status_code=400,
            detail=disabled_reason,
        )

    available_sip_rows = [row for row in sip_rows if row.get("available")]

    if not available_sip_rows:
        raise HTTPException(
            status_code=400,
            detail="No available SIP number. Check SIP registration and active calls.",
        )

    selected_sip_id = sip_trunk_id or available_sip_rows[0]["id"]

    selected_sip_row = next(
        (
            row for row in available_sip_rows
            if int(row["id"]) == int(selected_sip_id)
        ),
        None,
    )

    if not selected_sip_row:
        raise HTTPException(
            status_code=400,
            detail="Selected SIP number is not available.",
        )

    # Refuse to start unless the whole campaign can be paid for. The campaign is
    # left as a draft so it can be started again after topping up.
    needed_tokens = get_campaign_target_count(db, campaign)
    balance = billing_service.get_balance(db, user.company_id)

    if balance["spendable"] < needed_tokens:
        raise HTTPException(
            status_code=402,
            detail=(
                f"Not enough call tokens to run this campaign. "
                f"It needs {needed_tokens} but only {balance['spendable']} are available"
                + (f" ({balance['reserved']} held by running campaigns)" if balance["reserved"] else "")
                + ". Buy more tokens from your profile page, then start the campaign again."
            ),
        )

    campaign.selected_sip_trunk_id = int(selected_sip_row["id"])
    campaign.status = CampaignStatus.queued

    db.commit()
    db.refresh(campaign)

    print(
        f"WEB REAL START SAVED: campaign_id={campaign.id}, "
        f"selected_sip_trunk_id={campaign.selected_sip_trunk_id}"
    )

    task = run_campaign_task.delay(campaign.id)

    campaign.celery_task_id = task.id
    db.commit()

    return RedirectResponse(
        url=f"/web/campaigns/{campaign.id}",
        status_code=303,
    )