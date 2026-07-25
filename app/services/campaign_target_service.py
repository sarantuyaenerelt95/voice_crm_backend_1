# app/services/campaign_target_service.py

from __future__ import annotations

from typing import Optional, List, Any

from sqlalchemy.orm import Session

from app.models.campaign import Campaign
from app.models.campaign_target import CampaignTarget
from app.models.contact import Contact


def normalize_phone(phone: Optional[str]) -> str:
    return "".join(
        ch for ch in str(phone or "").strip()
        if ch.isdigit()
    )


def _resolve_campaign(
    db: Session,
    campaign_or_id: Any = None,
    campaign: Optional[Campaign] = None,
    campaign_id: Optional[int] = None,
) -> Campaign:
    if campaign is not None:
        return campaign

    if isinstance(campaign_or_id, Campaign):
        return campaign_or_id

    resolved_campaign_id = campaign_id

    if resolved_campaign_id is None and campaign_or_id is not None:
        resolved_campaign_id = int(campaign_or_id)

    if resolved_campaign_id is None:
        raise ValueError("campaign or campaign_id is required")

    campaign_obj = db.query(Campaign).filter(
        Campaign.id == resolved_campaign_id,
    ).first()

    if not campaign_obj:
        raise ValueError(f"Campaign not found: {resolved_campaign_id}")

    return campaign_obj


def sync_campaign_targets_from_contact_ids(
    db: Session,
    campaign_or_id: Any = None,
    contact_ids: Optional[List[int]] = None,
    *,
    campaign: Optional[Campaign] = None,
    campaign_id: Optional[int] = None,
    target_contact_ids: Optional[List[int]] = None,
) -> int:
    """
    Create frozen campaign target rows in the exact target order.

    Supports both call styles:

        sync_campaign_targets_from_contact_ids(db, campaign.id, contact_ids)

    and latest style:

        sync_campaign_targets_from_contact_ids(
            db=db,
            campaign=campaign,
            target_contact_ids=target_contact_ids,
        )
    """
    campaign_obj = _resolve_campaign(
        db=db,
        campaign_or_id=campaign_or_id,
        campaign=campaign,
        campaign_id=campaign_id,
    )

    ids = target_contact_ids if target_contact_ids is not None else contact_ids
    ids = list(ids or [])

    db.query(CampaignTarget).filter(
        CampaignTarget.campaign_id == campaign_obj.id,
        CampaignTarget.status == "pending",
        CampaignTarget.call_log_id.is_(None),
    ).delete(synchronize_session=False)

    if not ids:
        db.flush()
        return 0

    contacts = db.query(Contact).filter(
        Contact.company_id == campaign_obj.company_id,
        Contact.is_active == True,
        Contact.id.in_(ids),
    ).all()

    contacts_by_id = {
        contact.id: contact
        for contact in contacts
    }

    seen_phones = set()
    created = 0

    for position, contact_id in enumerate(ids):
        try:
            contact_id = int(contact_id)
        except (TypeError, ValueError):
            continue

        contact = contacts_by_id.get(contact_id)

        if not contact:
            continue

        phone = normalize_phone(contact.phone)

        if not phone:
            continue

        if phone in seen_phones:
            continue

        seen_phones.add(phone)

        db.add(
            CampaignTarget(
                campaign_id=campaign_obj.id,
                contact_id=contact.id,
                phone=phone,
                position=position,
                status="pending",
                attempts=0,
            )
        )

        created += 1

    db.flush()
    return created