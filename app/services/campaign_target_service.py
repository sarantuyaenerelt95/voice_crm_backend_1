from sqlalchemy.orm import Session

from app.models.campaign_target import CampaignTarget
from app.models.contact import Contact


def normalize_phone(phone: str | None) -> str:
    return "".join(ch for ch in str(phone or "").strip() if ch.isdigit())


def sync_campaign_targets_from_contact_ids(
    db: Session,
    campaign_id: int,
    contact_ids: list[int] | None,
) -> int:
    """Create the frozen campaign target rows in the exact target order."""
    ids = list(contact_ids or [])

    db.query(CampaignTarget).filter(
        CampaignTarget.campaign_id == campaign_id,
        CampaignTarget.status == "pending",
        CampaignTarget.call_log_id == None,
    ).delete(synchronize_session=False)

    if not ids:
        db.flush()
        return 0

    contacts = db.query(Contact).filter(Contact.id.in_(ids)).all()
    contacts_by_id = {contact.id: contact for contact in contacts}

    seen_phones = set()
    created = 0

    for position, contact_id in enumerate(ids):
        contact = contacts_by_id.get(contact_id)
        if not contact:
            continue

        phone = normalize_phone(contact.phone)
        if not phone or phone in seen_phones:
            continue

        seen_phones.add(phone)
        db.add(
            CampaignTarget(
                campaign_id=campaign_id,
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
