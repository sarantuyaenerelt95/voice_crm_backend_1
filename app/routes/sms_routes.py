# app/routes/sms_routes.py

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.contact import Contact
from app.models.sms_campaign import SMSCampaign
from app.models.sms_log import SMSLog
from app.models.sip_trunk import SIPTrunk
from app.models.user import User
from app.services.sms_service import estimate_sms_segments
from app.tasks.sms_tasks import send_sms_campaign_task


BASE_DIR = Path(__file__).resolve().parent.parent
TEMPLATES_DIR = BASE_DIR / "templates"

router = APIRouter(prefix="/web", tags=["SMS Broadcast"])
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


def utc_now():
    return datetime.now(timezone.utc)


def get_current_user_from_session(request: Request, db: Session) -> User:
    user_id = request.session.get("user_id")

    if not user_id:
        raise HTTPException(status_code=401, detail="Not logged in")

    user = db.query(User).filter(User.id == user_id).first()

    if not user:
        request.session.clear()
        raise HTTPException(status_code=401, detail="Invalid session")

    return user


def get_allowed_sip_trunks(db: Session, company_id: int):
    query = db.query(SIPTrunk).filter(
        SIPTrunk.is_active == True,
    )

    if hasattr(SIPTrunk, "is_applied"):
        query = query.filter(SIPTrunk.is_applied == True)

    if hasattr(SIPTrunk, "assigned_company_id"):
        query = query.filter(
            or_(
                SIPTrunk.assigned_company_id == None,
                SIPTrunk.assigned_company_id == company_id,
            )
        )

    return query.order_by(SIPTrunk.id.asc()).all()


def get_allowed_sip_trunk(db: Session, company_id: int, sip_trunk_id: int):
    query = db.query(SIPTrunk).filter(
        SIPTrunk.id == sip_trunk_id,
        SIPTrunk.is_active == True,
        SIPTrunk.sms_enabled == True,
    )

    if hasattr(SIPTrunk, "is_applied"):
        query = query.filter(SIPTrunk.is_applied == True)

    if hasattr(SIPTrunk, "assigned_company_id"):
        query = query.filter(
            or_(
                SIPTrunk.assigned_company_id == None,
                SIPTrunk.assigned_company_id == company_id,
            )
        )

    return query.first()


def refresh_sms_campaign_stats(db: Session, campaign: SMSCampaign):
    sent_count = (
        db.query(func.count(SMSLog.id))
        .filter(
            SMSLog.campaign_id == campaign.id,
            SMSLog.status.in_(["sent", "delivered"]),
        )
        .scalar()
        or 0
    )

    delivered_count = (
        db.query(func.count(SMSLog.id))
        .filter(
            SMSLog.campaign_id == campaign.id,
            SMSLog.status == "delivered",
        )
        .scalar()
        or 0
    )

    failed_count = (
        db.query(func.count(SMSLog.id))
        .filter(
            SMSLog.campaign_id == campaign.id,
            SMSLog.status == "failed",
        )
        .scalar()
        or 0
    )

    campaign.sent_count = int(sent_count)
    campaign.delivered_count = int(delivered_count)
    campaign.failed_count = int(failed_count)


@router.get("/sms-campaigns")
def sms_campaigns_page(request: Request, db: Session = Depends(get_db)):
    user = get_current_user_from_session(request, db)

    campaigns = db.query(SMSCampaign).filter(
        SMSCampaign.company_id == user.company_id,
    ).order_by(
        SMSCampaign.id.desc(),
    ).limit(100).all()

    return templates.TemplateResponse(
        "sms_campaigns.html",
        {
            "request": request,
            "user": user,
            "campaigns": campaigns,
        },
    )


@router.get("/sms-campaigns/new")
def sms_campaign_new_page(request: Request, db: Session = Depends(get_db)):
    user = get_current_user_from_session(request, db)

    sip_trunks = get_allowed_sip_trunks(db, user.company_id)

    contacts = db.query(Contact).filter(
        Contact.company_id == user.company_id,
        Contact.is_active == True,
    ).order_by(
        Contact.id.desc(),
    ).limit(500).all()

    return templates.TemplateResponse(
        "sms_campaign_new.html",
        {
            "request": request,
            "user": user,
            "sip_trunks": sip_trunks,
            "contacts": contacts,
        },
    )


@router.post("/sms-campaigns/create")
async def sms_campaign_create(request: Request, db: Session = Depends(get_db)):
    user = get_current_user_from_session(request, db)
    form = await request.form()

    name = str(form.get("name") or "").strip()
    message_text = str(form.get("message_text") or "").strip()
    sip_trunk_id_raw = str(form.get("sip_trunk_id") or "").strip()

    contact_ids = []

    for value in form.getlist("contact_ids"):
        value_text = str(value).strip()

        if value_text.isdigit():
            contact_ids.append(int(value_text))

    if not name:
        name = f"SMS Campaign {utc_now().strftime('%Y-%m-%d %H:%M')}"

    if not message_text:
        raise HTTPException(status_code=400, detail="SMS message text is required")

    if not sip_trunk_id_raw.isdigit():
        raise HTTPException(status_code=400, detail="Please select SIP number")

    sip_trunk = get_allowed_sip_trunk(
        db=db,
        company_id=user.company_id,
        sip_trunk_id=int(sip_trunk_id_raw),
    )

    if not sip_trunk:
        raise HTTPException(status_code=400, detail="Selected SIP number is not active/allowed")

    if not contact_ids:
        raise HTTPException(status_code=400, detail="Please select at least one contact")

    valid_contacts = db.query(Contact).filter(
        Contact.company_id == user.company_id,
        Contact.is_active == True,
        Contact.id.in_(contact_ids),
    ).all()

    valid_ids = {
        contact.id
        for contact in valid_contacts
    }

    ordered_contact_ids = [
        contact_id
        for contact_id in contact_ids
        if contact_id in valid_ids
    ]

    campaign = SMSCampaign(
        company_id=user.company_id,
        name=name,
        message_text=message_text,
        selected_provider_id=None,
        selected_sip_trunk_id=sip_trunk.id,
        target_contact_ids=ordered_contact_ids,
        status="draft",
        total_contacts=len(ordered_contact_ids),
        created_by=user.id,
    )

    db.add(campaign)
    db.commit()
    db.refresh(campaign)

    return RedirectResponse(
        url=f"/web/sms-campaigns/{campaign.id}",
        status_code=303,
    )


@router.get("/sms-campaigns/{campaign_id}")
def sms_campaign_detail_page(
    campaign_id: int,
    request: Request,
    db: Session = Depends(get_db),
):
    user = get_current_user_from_session(request, db)

    campaign = db.query(SMSCampaign).filter(
        SMSCampaign.id == campaign_id,
        SMSCampaign.company_id == user.company_id,
    ).first()

    if not campaign:
        raise HTTPException(status_code=404, detail="SMS campaign not found")

    refresh_sms_campaign_stats(db, campaign)
    db.commit()
    db.refresh(campaign)

    logs = db.query(SMSLog).filter(
        SMSLog.campaign_id == campaign.id,
    ).order_by(
        SMSLog.id.asc(),
    ).all()

    sip_trunk = None

    if getattr(campaign, "selected_sip_trunk_id", None):
        sip_trunk = db.query(SIPTrunk).filter(
            SIPTrunk.id == campaign.selected_sip_trunk_id,
        ).first()

    segments = estimate_sms_segments(campaign.message_text)

    return templates.TemplateResponse(
        "sms_campaign_detail.html",
        {
            "request": request,
            "user": user,
            "campaign": campaign,
            "sip_trunk": sip_trunk,
            "logs": logs,
            "segments": segments,
        },
    )


@router.post("/sms-campaigns/{campaign_id}/send")
def sms_campaign_send(
    campaign_id: int,
    request: Request,
    db: Session = Depends(get_db),
):
    user = get_current_user_from_session(request, db)

    campaign = db.query(SMSCampaign).filter(
        SMSCampaign.id == campaign_id,
        SMSCampaign.company_id == user.company_id,
    ).first()

    if not campaign:
        raise HTTPException(status_code=404, detail="SMS campaign not found")

    if not getattr(campaign, "selected_sip_trunk_id", None):
        raise HTTPException(status_code=400, detail="No SIP number selected for this SMS campaign")

    if campaign.status not in ("draft", "queued", "failed"):
        return RedirectResponse(
            url=f"/web/sms-campaigns/{campaign.id}",
            status_code=303,
        )

    campaign.status = "queued"
    campaign.started_at = None
    campaign.completed_at = None
    db.commit()

    send_sms_campaign_task.delay(campaign.id)

    return RedirectResponse(
        url=f"/web/sms-campaigns/{campaign.id}",
        status_code=303,
    )


@router.post("/sms-campaigns/{campaign_id}/cancel")
def sms_campaign_cancel(
    campaign_id: int,
    request: Request,
    db: Session = Depends(get_db),
):
    user = get_current_user_from_session(request, db)

    campaign = db.query(SMSCampaign).filter(
        SMSCampaign.id == campaign_id,
        SMSCampaign.company_id == user.company_id,
    ).first()

    if not campaign:
        raise HTTPException(status_code=404, detail="SMS campaign not found")

    if campaign.status in ("queued", "running"):
        campaign.status = "cancelled"
        campaign.completed_at = utc_now()

        db.query(SMSLog).filter(
            SMSLog.campaign_id == campaign.id,
            SMSLog.status.in_(["pending", "sending"]),
        ).update(
            {
                SMSLog.status: "cancelled",
                SMSLog.updated_at: utc_now(),
            },
            synchronize_session=False,
        )

        db.commit()

    return RedirectResponse(
        url=f"/web/sms-campaigns/{campaign.id}",
        status_code=303,
    )



@router.get("/sms-sip-settings")
def sms_sip_settings_page(request: Request, db: Session = Depends(get_db)):
    user = get_current_user_from_session(request, db)

    query = db.query(SIPTrunk).filter(
        SIPTrunk.is_active == True,
    )

    if hasattr(SIPTrunk, "is_applied"):
        query = query.filter(SIPTrunk.is_applied == True)

    if hasattr(SIPTrunk, "assigned_company_id"):
        query = query.filter(
            or_(
                SIPTrunk.assigned_company_id == None,
                SIPTrunk.assigned_company_id == user.company_id,
            )
        )

    sip_trunks = query.order_by(SIPTrunk.id.asc()).all()

    return templates.TemplateResponse(
        "sms_sip_settings.html",
        {
            "request": request,
            "user": user,
            "sip_trunks": sip_trunks,
        },
    )


@router.post("/sms-sip-settings/{sip_trunk_id}/update")
async def sms_sip_settings_update(
    sip_trunk_id: int,
    request: Request,
    db: Session = Depends(get_db),
):
    user = get_current_user_from_session(request, db)
    form = await request.form()

    query = db.query(SIPTrunk).filter(
        SIPTrunk.id == sip_trunk_id,
    )

    if hasattr(SIPTrunk, "assigned_company_id"):
        query = query.filter(
            or_(
                SIPTrunk.assigned_company_id == None,
                SIPTrunk.assigned_company_id == user.company_id,
            )
        )

    sip_trunk = query.first()

    if not sip_trunk:
        raise HTTPException(status_code=404, detail="SIP number not found")

    sms_mode = str(form.get("sms_mode") or "simulation").strip().lower()

    if sms_mode not in ("simulation",):
        sms_mode = "simulation"

    sip_trunk.sms_enabled = str(form.get("sms_enabled") or "").lower() in (
        "1",
        "true",
        "on",
        "yes",
    )
    sip_trunk.sms_mode = sms_mode
    sip_trunk.sms_sender_name = str(form.get("sms_sender_name") or "").strip() or sip_trunk.number

    db.commit()

    return RedirectResponse(
        url="/web/sms-sip-settings",
        status_code=303,
    )
