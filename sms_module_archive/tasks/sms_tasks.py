# app/tasks/sms_tasks.py

from __future__ import annotations

import os
import time
from datetime import datetime, timezone

from sqlalchemy import func, or_

from app.celery_app import celery_app
from app.database import SessionLocal
from app.models.contact import Contact
from app.models.sms_campaign import SMSCampaign
from app.models.sms_log import SMSLog
from app.models.sip_trunk import SIPTrunk
from app.services.sms_service import SMSService, normalize_phone


def utc_now():
    return datetime.now(timezone.utc)


SMS_SEND_INTERVAL_SEC = float(os.getenv("SMS_SEND_INTERVAL_SEC", "0.5"))


def refresh_sms_campaign_stats(db, campaign: SMSCampaign):
    sent_count = db.query(func.count(SMSLog.id)).filter(
        SMSLog.campaign_id == campaign.id,
        SMSLog.status.in_(["sent", "delivered"]),
    ).scalar() or 0

    delivered_count = db.query(func.count(SMSLog.id)).filter(
        SMSLog.campaign_id == campaign.id,
        SMSLog.status == "delivered",
    ).scalar() or 0

    failed_count = db.query(func.count(SMSLog.id)).filter(
        SMSLog.campaign_id == campaign.id,
        SMSLog.status == "failed",
    ).scalar() or 0

    campaign.sent_count = int(sent_count)
    campaign.delivered_count = int(delivered_count)
    campaign.failed_count = int(failed_count)


def get_allowed_sip_trunk(db, company_id: int, sip_trunk_id: int):
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


@celery_app.task(bind=True, name="send_sms_campaign_task")
def send_sms_campaign_task(self, sms_campaign_id: int):
    db = SessionLocal()

    try:
        campaign = db.query(SMSCampaign).filter(
            SMSCampaign.id == sms_campaign_id,
        ).first()

        if not campaign:
            print(f"SMS Celery: campaign not found. id={sms_campaign_id}")
            return

        if campaign.status not in ("draft", "queued"):
            print(
                f"SMS Celery: campaign {sms_campaign_id} ignored. "
                f"status={campaign.status}"
            )
            return

        sip_trunk_id = getattr(campaign, "selected_sip_trunk_id", None)

        if not sip_trunk_id:
            campaign.status = "failed"
            campaign.completed_at = utc_now()
            db.commit()
            print(f"SMS Celery: no SIP number selected for campaign {campaign.id}")
            return

        sip_trunk = get_allowed_sip_trunk(
            db=db,
            company_id=campaign.company_id,
            sip_trunk_id=int(sip_trunk_id),
        )

        if not sip_trunk:
            campaign.status = "failed"
            campaign.completed_at = utc_now()
            db.commit()
            print(f"SMS Celery: selected SIP number not active/allowed. campaign={campaign.id}")
            return

        campaign.status = "running"
        campaign.started_at = utc_now()
        campaign.completed_at = None
        db.commit()
        db.refresh(campaign)

        contact_ids = campaign.target_contact_ids or []

        contacts = db.query(Contact).filter(
            Contact.company_id == campaign.company_id,
            Contact.is_active == True,
            Contact.id.in_(contact_ids),
        ).all()

        contacts_by_id = {
            contact.id: contact
            for contact in contacts
        }

        ordered_contacts = []
        seen_phones = set()

        for contact_id in contact_ids:
            contact = contacts_by_id.get(contact_id)

            if not contact:
                continue

            phone = normalize_phone(getattr(contact, "phone", ""))

            if not phone:
                continue

            if phone in seen_phones:
                print(f"SMS Celery: duplicate phone skipped. phone={phone}")
                continue

            seen_phones.add(phone)
            ordered_contacts.append(contact)

        campaign.total_contacts = len(ordered_contacts)
        db.commit()

        print(
            f"SMS Celery: campaign {campaign.id} started. "
            f"contacts={len(ordered_contacts)}, "
            f"sip_number={sip_trunk.number}, "
            f"endpoint={sip_trunk.asterisk_endpoint}"
        )

        for contact in ordered_contacts:
            db.expire_all()

            campaign = db.query(SMSCampaign).filter(
                SMSCampaign.id == sms_campaign_id,
            ).first()

            if not campaign:
                break

            if campaign.status == "cancelled":
                print(f"SMS Celery: campaign {sms_campaign_id} cancelled.")
                break

            phone = normalize_phone(getattr(contact, "phone", ""))

            existing_log = db.query(SMSLog).filter(
                SMSLog.campaign_id == campaign.id,
                SMSLog.contact_id == contact.id,
            ).first()

            if existing_log:
                continue

            sms_log = SMSLog(
                campaign_id=campaign.id,
                contact_id=contact.id,
                provider_id=None,
                sip_trunk_id=sip_trunk.id,
                phone=phone,
                message_text=campaign.message_text,
                status="pending",
            )

            db.add(sms_log)
            db.commit()
            db.refresh(sms_log)

            SMSService.send_log(
                db=db,
                sms_log=sms_log,
                sip_trunk=sip_trunk,
                company_id=campaign.company_id,
            )

            campaign = db.query(SMSCampaign).filter(
                SMSCampaign.id == sms_campaign_id,
            ).first()

            if campaign:
                refresh_sms_campaign_stats(db, campaign)
                db.commit()

            time.sleep(SMS_SEND_INTERVAL_SEC)

        campaign = db.query(SMSCampaign).filter(
            SMSCampaign.id == sms_campaign_id,
        ).first()

        if campaign and campaign.status != "cancelled":
            refresh_sms_campaign_stats(db, campaign)
            campaign.status = "completed"
            campaign.completed_at = utc_now()
            db.commit()

        print(f"SMS Celery: campaign {sms_campaign_id} finished.")

    except Exception as e:
        db.rollback()

        print(f"SMS Celery error: {e}")

        campaign = db.query(SMSCampaign).filter(
            SMSCampaign.id == sms_campaign_id,
        ).first()

        if campaign and campaign.status != "cancelled":
            campaign.status = "failed"
            campaign.completed_at = utc_now()
            db.commit()

    finally:
        db.close()