from sqlalchemy.orm import Session

from app.models.call_log import CallLog, CallStatus
from app.models.sip_trunk import SIPTrunk
from app.services.asterisk_status import get_pjsip_registration_status


def get_available_sip_rows(db: Session, company_id: int | None = None) -> list[dict]:
    registration_statuses = get_pjsip_registration_status()

    query = db.query(SIPTrunk).filter(SIPTrunk.is_active == True)

    if hasattr(SIPTrunk, "is_applied"):
        query = query.filter(SIPTrunk.is_applied == True)

    if company_id is not None and hasattr(SIPTrunk, "assigned_company_id"):
        query = query.filter(
            (SIPTrunk.assigned_company_id == None)
            | (SIPTrunk.assigned_company_id == company_id)
        )

    rows = []

    for trunk in query.order_by(SIPTrunk.id.asc()).all():
        endpoint = str(trunk.asterisk_endpoint or "").strip()
        register_status = registration_statuses.get(endpoint, "Unknown")
        max_concurrent = max(1, int(trunk.max_concurrent or 1))
        active_calls = db.query(CallLog).filter(
            CallLog.trunk_id == trunk.id,
            CallLog.status == CallStatus.calling,
        ).count()
        free_slots = max(0, max_concurrent - active_calls)
        available = register_status == "Registered" and free_slots > 0

        rows.append(
            {
                "id": trunk.id,
                "number": trunk.number,
                "provider": getattr(trunk.provider, "value", trunk.provider),
                "endpoint": endpoint,
                "asterisk_endpoint": endpoint,
                "register_status": register_status,
                "max_concurrent": max_concurrent,
                "active_calls": active_calls,
                "free_slots": free_slots,
                "available": available,
                "trunk": trunk,
            }
        )

    return rows
