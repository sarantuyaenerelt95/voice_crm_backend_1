# app/services/sip_availability.py

from __future__ import annotations

from typing import Optional, List, Dict, Any

from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.models.call_log import CallLog, CallStatus
from app.models.sip_trunk import SIPTrunk
from app.services.asterisk_status import get_pjsip_registration_status


def has_model_column(model, column_name: str) -> bool:
    return column_name in {column.name for column in model.__table__.columns}


def normalize_register_status(value: Any) -> str:
    return str(value or "Unknown").strip()


def is_registered_status(value: Any) -> bool:
    return normalize_register_status(value) == "Registered"


def get_active_call_count(db: Session, trunk_id: int) -> int:
    return db.query(CallLog).filter(
        CallLog.trunk_id == trunk_id,
        CallLog.status == CallStatus.calling,
    ).count()


def get_available_sip_rows(
    db: Session,
    company_id: Optional[int] = None,
) -> List[Dict[str, Any]]:
    registration_statuses = get_pjsip_registration_status()
    registration_error = registration_statuses.get("_error")

    query = db.query(SIPTrunk).filter(
        SIPTrunk.is_active.is_(True),
    )

    if has_model_column(SIPTrunk, "is_applied"):
        query = query.filter(
            SIPTrunk.is_applied.is_(True),
        )

    if company_id is not None and has_model_column(SIPTrunk, "assigned_company_id"):
        query = query.filter(
            or_(
                SIPTrunk.assigned_company_id.is_(None),
                SIPTrunk.assigned_company_id == company_id,
            )
        )

    rows = []

    for trunk in query.order_by(SIPTrunk.id.asc()).all():
        endpoint = str(trunk.asterisk_endpoint or "").strip()

        register_status = normalize_register_status(
            registration_statuses.get(endpoint, "Unknown")
        )

        max_concurrent = max(1, int(trunk.max_concurrent or 1))
        active_calls = get_active_call_count(db, trunk.id)
        free_slots = max(0, max_concurrent - active_calls)

        available = (
            is_registered_status(register_status)
            and free_slots > 0
        )

        rows.append({
            "id": trunk.id,
            "number": trunk.number,
            "provider": getattr(trunk.provider, "value", trunk.provider),
            "endpoint": endpoint,
            "asterisk_endpoint": endpoint,
            "register_status": register_status,
            "registration_error": registration_error,
            "max_concurrent": max_concurrent,
            "active_calls": active_calls,
            "free_slots": free_slots,
            "available": available,
            "trunk": trunk,
        })

    return rows