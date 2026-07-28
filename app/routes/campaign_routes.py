# app/routes/campaign_routes.py

from __future__ import annotations

from typing import List
import os
import time
import shutil
import re
import csv
import io
from pathlib import Path
from datetime import datetime, timezone

from fastapi import APIRouter, UploadFile, File, HTTPException, Depends
from fastapi.responses import StreamingResponse
from sqlalchemy import func
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError

from app.config import settings
from app.database import get_db
from app.models.user import User
from app.models.contact import Contact
from app.models.sip_trunk import SIPTrunk
from app.models.audio_file import AudioFile, AudioSource
from app.models.campaign import Campaign, CampaignStatus
from app.models.call_log import CallLog, CallStatus
from app.models.campaign_target import CampaignTarget

from app.routes.auth_routes import get_current_user
from app.services.audio_converter import AudioConverter
from app.services.campaign_target_service import sync_campaign_targets_from_contact_ids
from app.services.sip_availability import get_available_sip_rows
from app.tasks.campaign_tasks import run_campaign_task

from app.schemas.campaign import (
    SIPTrunkCreate,
    SIPTrunkResponse,
    ContactCreate,
    ContactResponse,
    ContactImportResponse,
    CampaignCreate,
    CampaignResponse,
    CampaignDryRunResponse,
    CampaignStatusResponse,
    CallLogResponse,
    AudioFileResponse,
    CampaignSimulateResponse,
    CampaignSummaryResponse,
)

from app.services.audio_capacity import (
    check_audio_duration,
    check_audio_storage_capacity,
    safe_remove_file,
)


router = APIRouter(prefix="/campaigns", tags=["campaigns"])

ASTERISK_SOUNDS_DIR = settings.ASTERISK_SOUNDS_DIR
ALLOWED_EXTENSIONS = {".mp3", ".wav", ".m4a", ".ogg", ".flac"}


def safe_audio_name(filename: str) -> str:
    base = Path(filename).stem.lower()
    base = re.sub(r"[^a-z0-9_]+", "_", base)
    return base.strip("_") or "audio"


def get_campaign_target_count(db: Session, campaign: Campaign) -> int:
    target_count = db.query(func.count(CampaignTarget.id)).filter(
        CampaignTarget.campaign_id == campaign.id,
    ).scalar() or 0

    if target_count > 0:
        return int(target_count)

    return len(campaign.target_contact_ids or [])


def get_campaign_contacts(
    request: CampaignCreate,
    company_id: int,
    db: Session,
) -> List[Contact]:
    if request.contact_limit is not None and request.contact_limit <= 0:
        raise HTTPException(status_code=400, detail="contact_limit must be greater than 0")

    base_query = db.query(Contact).filter(
        Contact.company_id == company_id,
        Contact.is_active == True,
    )

    if request.contact_ids:
        seen_ids = set()
        requested_ids = []

        for contact_id in request.contact_ids:
            if contact_id <= 0:
                raise HTTPException(status_code=400, detail="contact_ids must be positive integers")

            if contact_id not in seen_ids:
                seen_ids.add(contact_id)
                requested_ids.append(contact_id)

        contacts = base_query.filter(Contact.id.in_(requested_ids)).all()
        contacts_by_id = {contact.id: contact for contact in contacts}
        missing_ids = [contact_id for contact_id in requested_ids if contact_id not in contacts_by_id]

        if missing_ids:
            raise HTTPException(
                status_code=400,
                detail=f"Contacts not found or inactive: {missing_ids}",
            )

        ordered_contacts = [contacts_by_id[contact_id] for contact_id in requested_ids]

        if request.contact_limit is not None:
            ordered_contacts = ordered_contacts[:request.contact_limit]

        return ordered_contacts

    query = base_query.order_by(Contact.created_at.asc(), Contact.id.asc())

    if request.contact_limit is not None:
        query = query.limit(request.contact_limit)

    return query.all()


def get_campaign_target_contacts(campaign: Campaign, db: Session) -> List[Contact]:
    target_contact_ids = campaign.target_contact_ids or []

    if not target_contact_ids:
        return []

    contacts = db.query(Contact).filter(
        Contact.company_id == campaign.company_id,
        Contact.is_active == True,
        Contact.id.in_(target_contact_ids),
    ).all()

    contacts_by_id = {contact.id: contact for contact in contacts}

    deduped_contacts = []
    seen_phones = set()

    for contact_id in target_contact_ids:
        contact = contacts_by_id.get(contact_id)

        if not contact:
            continue

        normalized_phone = "".join(
            ch for ch in str(contact.phone or "").strip()
            if ch.isdigit()
        )

        if not normalized_phone:
            continue

        if normalized_phone in seen_phones:
            print(
                f"API: duplicate target phone skipped. "
                f"campaign_id={campaign.id}, phone={normalized_phone}, contact_id={contact.id}"
            )
            continue

        seen_phones.add(normalized_phone)
        deduped_contacts.append(contact)

    return deduped_contacts


def build_campaign_status(campaign: Campaign, db: Session) -> dict:
    counts = dict(
        db.query(CallLog.status, func.count(CallLog.id))
        .filter(CallLog.campaign_id == campaign.id)
        .group_by(CallLog.status)
        .all()
    )

    calling = counts.get(CallStatus.calling, 0)
    completed = counts.get(CallStatus.completed, 0)
    failed = counts.get(CallStatus.failed, 0)
    busy = counts.get(CallStatus.busy, 0)
    no_answer = counts.get(CallStatus.no_answer, 0)
    congestion = counts.get(CallStatus.congestion, 0)

    finished = completed + failed + busy + no_answer + congestion
    total = campaign.total_contacts or finished or 0
    progress_percent = min(round((finished / total) * 100, 2), 100.0) if total else 0.0

    return {
        "campaign_id": campaign.id,
        "status": campaign.status,
        "total_contacts": total,
        "calling": calling,
        "completed": completed,
        "failed": failed,
        "busy": busy,
        "no_answer": no_answer,
        "congestion": congestion,
        "finished": finished,
        "progress_percent": progress_percent,
    }


@router.post("/trunks", response_model=SIPTrunkResponse)
def create_trunk(
    request: SIPTrunkCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    trunk = SIPTrunk(
        number=request.number,
        provider=request.provider,
        sip_host=request.sip_host,
        sip_username=request.sip_username,
        sip_password=request.sip_password,
        asterisk_endpoint=request.asterisk_endpoint,
        max_concurrent=request.max_concurrent or 3,
        is_active=True,
        managed_by_crm=True,
        current_active_calls=0,
        is_applied=False,
    )

    try:
        db.add(trunk)
        db.commit()
        db.refresh(trunk)
        return trunk
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=400, detail="SIP trunk already exists")


@router.get("/trunks", response_model=List[SIPTrunkResponse])
def list_trunks(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return db.query(SIPTrunk).filter(SIPTrunk.is_active == True).all()


@router.post("/contacts", response_model=ContactResponse)
def create_contact(
    request: ContactCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    contact = Contact(
        company_id=current_user.company_id,
        phone=request.phone,
        full_name=request.full_name,
        notes=request.notes,
        is_active=True,
    )

    try:
        db.add(contact)
        db.commit()
        db.refresh(contact)
        return contact
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=400, detail="Phone already exists for this company")


@router.get("/contacts", response_model=List[ContactResponse])
def list_contacts(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return db.query(Contact).filter(
        Contact.company_id == current_user.company_id,
        Contact.is_active == True,
    ).all()


@router.post("/audio-files/upload")
def upload_audio_file(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    ext = Path(file.filename).suffix.lower()

    if ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail="Only mp3, wav, m4a, ogg, flac files are allowed",
        )

    os.makedirs(ASTERISK_SOUNDS_DIR, exist_ok=True)

    safe_name = safe_audio_name(file.filename)
    unique_name = f"{safe_name}_{int(time.time())}"

    temp_input_path = f"/tmp/{unique_name}{ext}"
    output_filename = f"{unique_name}.wav"
    output_path = os.path.join(ASTERISK_SOUNDS_DIR, output_filename)

    try:
        with open(temp_input_path, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)

        AudioConverter.convert_to_wav_8k_mono(
            input_path=temp_input_path,
            output_path=output_path,
        )

        os.chmod(output_path, 0o644)

        duration_sec = AudioConverter.get_duration_sec(output_path)
        check_audio_duration(duration_sec)

        file_size_bytes = os.path.getsize(output_path)

        check_audio_storage_capacity(
            db,
            current_user.company_id,
            file_size_bytes,
        )

        audio = AudioFile(
            company_id=current_user.company_id,
            filename=unique_name,
            file_path=output_path,
            source=AudioSource.upload,
            duration_sec=duration_sec,
            file_size_bytes=file_size_bytes,
        )

        db.add(audio)
        db.commit()
        db.refresh(audio)

        return {
            "id": audio.id,
            "company_id": audio.company_id,
            "filename": audio.filename,
            "file_path": audio.file_path,
            "duration_sec": audio.duration_sec,
            "playback_name": f"custom/{audio.filename}",
        }

    except HTTPException:
        db.rollback()
        safe_remove_file(output_path)
        raise

    except Exception as e:
        db.rollback()
        safe_remove_file(output_path)
        raise HTTPException(status_code=500, detail=str(e))

    finally:
        safe_remove_file(temp_input_path)


@router.get("/audio-files", response_model=List[AudioFileResponse])
def list_audio_files(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return db.query(AudioFile).filter(
        AudioFile.company_id == current_user.company_id,
    ).order_by(AudioFile.created_at.desc()).all()


@router.post("", response_model=CampaignResponse)
def create_campaign(
    request: CampaignCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    audio = db.query(AudioFile).filter(
        AudioFile.id == request.audio_file_id,
        AudioFile.company_id == current_user.company_id,
    ).first()

    if not audio:
        raise HTTPException(status_code=404, detail="Audio file not found")

    contacts = get_campaign_contacts(
        request=request,
        company_id=current_user.company_id,
        db=db,
    )

    if not contacts:
        raise HTTPException(status_code=400, detail="No active contacts found for campaign")

    target_contact_ids = [contact.id for contact in contacts]

    campaign = Campaign(
        company_id=current_user.company_id,
        created_by_id=current_user.id,
        audio_file_id=audio.id,
        name=request.name,
        status=CampaignStatus.draft,
        total_contacts=len(target_contact_ids),
        target_contact_ids=target_contact_ids,
    )

    db.add(campaign)
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

    return campaign


@router.post("/contacts/import", response_model=ContactImportResponse)
def import_contacts(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    filename = (file.filename or "").lower()

    if not (filename.endswith(".csv") or filename.endswith(".txt")):
        raise HTTPException(status_code=400, detail="Only CSV or TXT file allowed")

    try:
        content = file.file.read().decode("utf-8-sig")
    except UnicodeDecodeError:
        raise HTTPException(status_code=400, detail="File must be UTF-8 encoded")

    rows = []

    if filename.endswith(".csv"):
        stream = io.StringIO(content)
        dict_reader = csv.DictReader(stream)
        fieldnames = [name.strip().lower() for name in (dict_reader.fieldnames or [])]

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
            Contact.company_id == current_user.company_id,
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
            company_id=current_user.company_id,
            phone=phone,
            full_name=row.get("full_name") or None,
            notes=row.get("notes") or None,
            is_active=True,
        )

        db.add(contact)
        seen_phones.add(phone)
        created += 1

    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=400, detail="Duplicate contact found during import")

    return {
        "filename": file.filename,
        "created": created,
        "skipped": skipped,
        "total_rows": len(rows),
    }


@router.get("", response_model=List[CampaignResponse])
def list_campaigns(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return db.query(Campaign).filter(
        Campaign.company_id == current_user.company_id,
    ).order_by(Campaign.created_at.desc()).all()


@router.get("/{campaign_id}/calls", response_model=List[CallLogResponse])
def list_campaign_calls(
    campaign_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    campaign = db.query(Campaign).filter(
        Campaign.id == campaign_id,
        Campaign.company_id == current_user.company_id,
    ).first()

    if not campaign:
        raise HTTPException(status_code=404, detail="Campaign not found")

    return db.query(CallLog).filter(
        CallLog.campaign_id == campaign.id,
    ).order_by(CallLog.started_at.desc()).all()


@router.post("/{campaign_id}/dry-run", response_model=CampaignDryRunResponse)
def dry_run_campaign(
    campaign_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    if not settings.ENABLE_SIMULATION:
        raise HTTPException(
            status_code=403,
            detail="Dry-run is disabled in this environment.",
        )

    campaign = db.query(Campaign).filter(
        Campaign.id == campaign_id,
        Campaign.company_id == current_user.company_id,
    ).first()

    if not campaign:
        raise HTTPException(status_code=404, detail="Campaign not found")

    contacts = get_campaign_target_contacts(campaign, db)

    return {
        "campaign_id": campaign.id,
        "campaign_name": campaign.name,
        "target_count": len(contacts),
        "contacts": contacts,
    }


@router.get("/{campaign_id}/report.csv")
def export_campaign_report(
    campaign_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    campaign = db.query(Campaign).filter(
        Campaign.id == campaign_id,
        Campaign.company_id == current_user.company_id,
    ).first()

    if not campaign:
        raise HTTPException(status_code=404, detail="Campaign not found")

    calls = db.query(CallLog).filter(
        CallLog.campaign_id == campaign.id,
    ).order_by(CallLog.started_at.asc(), CallLog.id.asc()).all()

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["phone", "status", "duration_sec", "hangup_cause", "answered_at", "ended_at"])

    for call in calls:
        writer.writerow([
            call.phone,
            call.status.value if call.status else "",
            call.duration_sec if call.duration_sec is not None else "",
            call.hangup_cause if call.hangup_cause is not None else "",
            call.answered_at.isoformat() if call.answered_at else "",
            call.ended_at.isoformat() if call.ended_at else "",
        ])

    output.seek(0)
    filename = f"campaign_{campaign.id}_report.csv"

    return StreamingResponse(
        output,
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/{campaign_id}/status", response_model=CampaignStatusResponse)
def get_campaign_status(
    campaign_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    campaign = db.query(Campaign).filter(
        Campaign.id == campaign_id,
        Campaign.company_id == current_user.company_id,
    ).first()

    if not campaign:
        raise HTTPException(status_code=404, detail="Campaign not found")

    return build_campaign_status(campaign, db)


@router.get("/{campaign_id}/summary", response_model=CampaignSummaryResponse)
def get_campaign_summary(
    campaign_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    campaign = db.query(Campaign).filter(
        Campaign.id == campaign_id,
        Campaign.company_id == current_user.company_id,
    ).first()

    if not campaign:
        raise HTTPException(status_code=404, detail="Campaign not found")

    status_data = build_campaign_status(campaign, db)

    recent_calls = db.query(CallLog).filter(
        CallLog.campaign_id == campaign.id,
    ).order_by(CallLog.started_at.desc(), CallLog.id.desc()).limit(10).all()

    return {
        "campaign_id": campaign.id,
        "name": campaign.name,
        "status": campaign.status.value if campaign.status else "",
        "total_contacts": status_data["total_contacts"],
        "calling": status_data["calling"],
        "completed": status_data["completed"],
        "failed": status_data["failed"],
        "busy": status_data["busy"],
        "no_answer": status_data["no_answer"],
        "congestion": status_data["congestion"],
        "finished": status_data["finished"],
        "progress_percent": status_data["progress_percent"],
        "recent_calls": [
            {
                "phone": call.phone,
                "status": call.status.value if call.status else "",
                "duration_sec": call.duration_sec,
                "hangup_cause": call.hangup_cause,
            }
            for call in recent_calls
        ],
    }


@router.get("/{campaign_id}", response_model=CampaignResponse)
def get_campaign(
    campaign_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    campaign = db.query(Campaign).filter(
        Campaign.id == campaign_id,
        Campaign.company_id == current_user.company_id,
    ).first()

    if not campaign:
        raise HTTPException(status_code=404, detail="Campaign not found")

    return campaign


@router.post("/{campaign_id}/start")
def start_campaign(
    campaign_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    campaign = db.query(Campaign).filter(
        Campaign.id == campaign_id,
        Campaign.company_id == current_user.company_id,
    ).first()

    if not campaign:
        raise HTTPException(status_code=404, detail="Campaign not found")

    status_value = str(campaign.status)
    if "." in status_value:
        status_value = status_value.split(".")[-1]

    if status_value != "draft":
        raise HTTPException(
            status_code=400,
            detail="Only draft campaigns can be started.",
        )

    target_count = get_campaign_target_count(db, campaign)

    if target_count <= 0:
        raise HTTPException(
            status_code=400,
            detail="Campaign has no target contacts. Create a new campaign first.",
        )

    target_contacts = get_campaign_target_contacts(campaign, db)

    if not target_contacts:
        raise HTTPException(
            status_code=400,
            detail="No active frozen target contacts found for campaign",
        )

    if not campaign.audio_file:
        raise HTTPException(
            status_code=400,
            detail="Campaign has no audio file.",
        )

    sip_rows = get_available_sip_rows(db, campaign.company_id)
    available_sip_rows = [row for row in sip_rows if row.get("available")]

    if not available_sip_rows:
        raise HTTPException(
            status_code=400,
            detail="No registered available SIP number.",
        )

    selected_sip_row = available_sip_rows[0]

    campaign.selected_sip_trunk_id = int(selected_sip_row["id"])
    campaign.total_contacts = len(target_contacts)
    campaign.status = CampaignStatus.queued

    db.commit()
    db.refresh(campaign)

    task = run_campaign_task.delay(campaign.id)

    campaign.celery_task_id = task.id
    db.commit()
    db.refresh(campaign)

    return {
        "message": "Campaign queued",
        "campaign_id": campaign.id,
        "task_id": task.id,
        "status": campaign.status,
        "total_contacts": campaign.total_contacts,
        "selected_sip_trunk_id": campaign.selected_sip_trunk_id,
    }


@router.post("/{campaign_id}/cancel")
def cancel_campaign(
    campaign_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    campaign = db.query(Campaign).filter(
        Campaign.id == campaign_id,
        Campaign.company_id == current_user.company_id,
    ).first()

    if not campaign:
        raise HTTPException(status_code=404, detail="Campaign not found")

    campaign.status = CampaignStatus.cancelled
    campaign.completed_at = datetime.now(timezone.utc)

    db.commit()
    db.refresh(campaign)

    return {
        "message": "Campaign cancelled",
        "campaign_id": campaign.id,
        "status": campaign.status,
    }


@router.post("/{campaign_id}/simulate", response_model=CampaignSimulateResponse)
def simulate_campaign(
    campaign_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    if not settings.ENABLE_SIMULATION:
        raise HTTPException(
            status_code=403,
            detail="Simulation is disabled in this environment.",
        )

    print("SIMULATION ONLY - NO ASTERISK CALL")

    campaign = db.query(Campaign).filter(
        Campaign.id == campaign_id,
        Campaign.company_id == current_user.company_id,
    ).first()

    if not campaign:
        raise HTTPException(status_code=404, detail="Campaign not found")

    target_ids = campaign.target_contact_ids or []

    if not target_ids:
        raise HTTPException(
            status_code=400,
            detail="Campaign has no frozen target_contact_ids",
        )

    contacts = db.query(Contact).filter(
        Contact.company_id == current_user.company_id,
        Contact.id.in_(target_ids),
        Contact.is_active == True,
    ).all()

    contact_map = {contact.id: contact for contact in contacts}
    ordered_contacts = [
        contact_map[contact_id]
        for contact_id in target_ids
        if contact_id in contact_map
    ]

    if not ordered_contacts:
        raise HTTPException(
            status_code=400,
            detail="No valid active contacts found for this campaign",
        )

    trunk = db.query(SIPTrunk).filter(
        SIPTrunk.is_active == True,
    ).first()

    if not trunk:
        raise HTTPException(
            status_code=400,
            detail="No active SIP trunk found for simulation CallLog trunk_id",
        )

    now = datetime.now(timezone.utc)

    audio_duration = 0
    if campaign.audio_file and campaign.audio_file.duration_sec:
        audio_duration = campaign.audio_file.duration_sec

    completed_count = 0

    for contact in ordered_contacts:
        existing_call = db.query(CallLog).filter(
            CallLog.campaign_id == campaign.id,
            CallLog.contact_id == contact.id,
        ).first()

        if existing_call:
            call = existing_call
        else:
            call = CallLog(
                campaign_id=campaign.id,
                contact_id=contact.id,
                trunk_id=trunk.id,
                phone=contact.phone,
            )
            db.add(call)

        call.status = CallStatus.completed
        call.duration_sec = audio_duration
        call.hangup_cause = 16
        call.started_at = now
        call.answered_at = now
        call.ended_at = now

        completed_count += 1

    campaign.total_contacts = len(ordered_contacts)
    campaign.completed_calls = completed_count
    campaign.failed_calls = 0
    campaign.busy_calls = 0
    campaign.no_answer_calls = 0
    campaign.status = CampaignStatus.completed
    campaign.completed_at = now

    db.commit()

    return {
        "campaign_id": campaign.id,
        "simulated": True,
        "target_count": len(ordered_contacts),
        "completed_calls": completed_count,
    }