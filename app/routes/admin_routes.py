# app/routes/admin_routes.py

from __future__ import annotations

from datetime import datetime, timezone
import re

from fastapi import APIRouter, Request, Depends, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.database import get_db
from app.i18n import templating as i18n_templating
from app.models.user import User
from app.models.sip_trunk import SIPTrunk
from app.models.call_log import CallLog, CallStatus
from app.services.asterisk_trunk_generator import generate_pjsip_config, apply_pjsip_config
from app.services.asterisk_status import get_pjsip_registration_status


router = APIRouter(prefix="/admin", tags=["admin"])
templates = Jinja2Templates(directory="app/templates")

# Gives every template t(), lang and languages.
i18n_templating.install(templates)


def utc_now():
    return datetime.now(timezone.utc)


def model_columns(model):
    return {column.name for column in model.__table__.columns}


def get_trunk_active_call_count(db: Session, trunk_id: int) -> int:
    return db.query(CallLog).filter(
        CallLog.trunk_id == trunk_id,
        CallLog.status == CallStatus.calling,
    ).count()


def apply_failed_error(message: str, generate_result, apply_result) -> HTTPException:
    """Report an Asterisk apply failure without leaking internals to the browser.

    generate_result/apply_result contain server file paths and raw stderr, so
    they go to the log; the user gets a short message and the trunk's
    last_apply_error field holds the detail for an admin to read.
    """
    print(
        f"ADMIN APPLY FAILED: {message} "
        f"generate={generate_result} apply={apply_result}"
    )

    return HTTPException(status_code=500, detail=message)


def regenerate_and_apply(db: Session):
    """Write the CRM-managed PJSIP config and reload Asterisk."""
    trunks = get_active_managed_trunks(db)

    generate_result = generate_pjsip_config(trunks)
    apply_result = apply_pjsip_config()

    return generate_result, apply_result


def get_current_super_admin(request: Request, db: Session) -> User:
    user_id = request.session.get("user_id")

    if not user_id:
        raise HTTPException(status_code=401, detail="Not logged in")

    user = db.query(User).filter(User.id == user_id).first()

    if not user:
        request.session.clear()
        raise HTTPException(status_code=401, detail="Invalid session")

    role_raw = getattr(user, "role", None)

    if hasattr(role_raw, "value"):
        role_value = role_raw.value
    elif role_raw is not None:
        role_value = str(role_raw)
    else:
        role_value = ""

    role_value = role_value.lower().strip()

    if role_value != "owner":
        raise HTTPException(status_code=403, detail="Owner only")

    return user


def get_active_managed_trunks(db: Session):
    return (
        db.query(SIPTrunk)
        .filter(
            SIPTrunk.is_active == True,
            SIPTrunk.managed_by_crm == True,
        )
        .order_by(SIPTrunk.id.asc())
        .all()
    )


def make_voicecrm_endpoint(provider: str, sip_number: str) -> str:
    provider = str(provider or "sip").lower().strip()
    sip_number = str(sip_number or "").strip()

    endpoint = f"vc_{provider}_{sip_number}"

    # Keep only letters, numbers, underscore for Asterisk section name
    endpoint = re.sub(r"[^A-Za-z0-9_]", "_", endpoint)
    endpoint = re.sub(r"_+", "_", endpoint)
    endpoint = endpoint.strip("_")

    if not endpoint.startswith("vc_"):
        endpoint = f"vc_{endpoint}"

    return endpoint[:80]


@router.get("/dashboard")
def admin_dashboard(
    request: Request,
    db: Session = Depends(get_db),
):
    get_current_super_admin(request, db)

    return RedirectResponse(
        url="/admin/sip-numbers",
        status_code=303,
    )


@router.get("/sip-numbers", response_class=HTMLResponse)
def admin_sip_numbers(
    request: Request,
    db: Session = Depends(get_db),
):
    admin = get_current_super_admin(request, db)

    trunks = (
        db.query(SIPTrunk)
        .order_by(
            SIPTrunk.is_active.desc(),
            SIPTrunk.id.asc(),
        )
        .all()
    )

    registration_statuses = get_pjsip_registration_status()

    return templates.TemplateResponse(
        "admin_sip_numbers.html",
        {
            "request": request,
            "admin": admin,
            "trunks": trunks,
            "rows": trunks,
            "registration_statuses": registration_statuses,
        },
    )


@router.post("/sip-numbers/add")
@router.post("/sip-numbers/add-apply")
async def admin_add_apply_sip_number(
    request: Request,
    db: Session = Depends(get_db),
):
    get_current_super_admin(request, db)

    form = await request.form()

    sip_number = (
        form.get("sip_number")
        or form.get("number")
        or ""
    ).strip()

    provider = (
        form.get("provider")
        or "cally"
    ).strip().lower()

    sip_host = (
        form.get("sip_host")
        or form.get("host")
        or ""
    ).strip()

    sip_domain = (
        form.get("sip_domain")
        or sip_host
        or ""
    ).strip()

    sip_username = (
        form.get("sip_username")
        or form.get("username")
        or ""
    ).strip()

    sip_password = (
        form.get("sip_password")
        or form.get("password")
        or ""
    )

    asterisk_endpoint = make_voicecrm_endpoint(provider, sip_number)

    try:
        max_concurrent = int(form.get("max_concurrent") or 1)
    except ValueError:
        max_concurrent = 1

    if not sip_number:
        raise HTTPException(status_code=400, detail="SIP number is required")

    if not sip_host:
        raise HTTPException(status_code=400, detail="SIP host is required")

    if not sip_username:
        raise HTTPException(status_code=400, detail="SIP username is required")

    if not sip_password:
        raise HTTPException(status_code=400, detail="SIP password is required")

    existing_trunk = (
        db.query(SIPTrunk)
        .filter(SIPTrunk.number == sip_number)
        .first()
    )

    if existing_trunk and existing_trunk.is_active:
        raise HTTPException(
            status_code=400,
            detail="This SIP number is already active in CRM. Remove it first before adding again.",
        )

    cols = model_columns(SIPTrunk)

    data = {}

    if "number" in cols:
        data["number"] = sip_number

    if "provider" in cols:
        data["provider"] = provider

    if "sip_host" in cols:
        data["sip_host"] = sip_host

    if "sip_domain" in cols:
        data["sip_domain"] = sip_domain

    if "sip_username" in cols:
        data["sip_username"] = sip_username

    if "sip_password" in cols:
        data["sip_password"] = sip_password

    if "asterisk_endpoint" in cols:
        data["asterisk_endpoint"] = asterisk_endpoint

    if "max_concurrent" in cols:
        data["max_concurrent"] = max_concurrent

    if "is_active" in cols:
        data["is_active"] = True

    if "managed_by_crm" in cols:
        data["managed_by_crm"] = True

    if "current_active_calls" in cols:
        data["current_active_calls"] = 0

    if "is_applied" in cols:
        data["is_applied"] = False

    if existing_trunk:
        for key, value in data.items():
            setattr(existing_trunk, key, value)

        existing_trunk.is_active = True

        if "is_applied" in cols:
            existing_trunk.is_applied = False

        if "removed_at" in cols:
            existing_trunk.removed_at = None

        if "last_apply_error" in cols:
            existing_trunk.last_apply_error = None

        trunk = existing_trunk
    else:
        trunk = SIPTrunk(**data)
        db.add(trunk)

    db.commit()
    db.refresh(trunk)

    generate_result, apply_result = regenerate_and_apply(db)

    if apply_result["ok"]:
        if "is_applied" in cols:
            trunk.is_applied = True

        if "applied_at" in cols:
            trunk.applied_at = utc_now()

        if "last_apply_error" in cols:
            trunk.last_apply_error = None

        db.commit()

        return RedirectResponse(
            url="/admin/sip-numbers",
            status_code=303,
        )

    if "is_applied" in cols:
        trunk.is_applied = False

    if "last_apply_error" in cols:
        trunk.last_apply_error = (
            apply_result.get("stderr")
            or apply_result.get("stdout")
            or "Unknown apply error"
        )

    db.commit()

    raise apply_failed_error(
        "SIP number was saved but could not be applied to Asterisk.",
        generate_result,
        apply_result,
    )


@router.post("/sip-numbers/{trunk_id}/enable")
def admin_enable_sip_number(
    trunk_id: int,
    request: Request,
    db: Session = Depends(get_db),
):
    get_current_super_admin(request, db)

    trunk = (
        db.query(SIPTrunk)
        .filter(SIPTrunk.id == trunk_id)
        .first()
    )

    if not trunk:
        raise HTTPException(status_code=404, detail="SIP trunk not found")

    cols = model_columns(SIPTrunk)

    previous_is_active = trunk.is_active

    trunk.is_active = True

    if "managed_by_crm" in cols:
        trunk.managed_by_crm = True

    if "is_applied" in cols:
        trunk.is_applied = False

    if "removed_at" in cols:
        trunk.removed_at = None

    if "last_apply_error" in cols:
        trunk.last_apply_error = None

    db.commit()

    generate_result, apply_result = regenerate_and_apply(db)

    if apply_result["ok"]:
        if "is_applied" in cols:
            trunk.is_applied = True

        if "applied_at" in cols:
            trunk.applied_at = utc_now()

        if "last_apply_error" in cols:
            trunk.last_apply_error = None

        db.commit()

        return RedirectResponse(
            url="/admin/sip-numbers",
            status_code=303,
        )

    # Asterisk never reloaded, so this trunk is not really usable. Revert so the
    # campaign scheduler does not pick a trunk that Asterisk cannot dial through.
    trunk.is_active = previous_is_active

    if "is_applied" in cols:
        trunk.is_applied = False

    if "last_apply_error" in cols:
        trunk.last_apply_error = (
            apply_result.get("stderr")
            or apply_result.get("stdout")
            or "Unknown enable apply error"
        )

    db.commit()

    generate_pjsip_config(get_active_managed_trunks(db))

    raise apply_failed_error(
        "Could not apply to Asterisk. The SIP number was left disabled.",
        generate_result,
        apply_result,
    )

@router.post("/sip-numbers/{trunk_id}/disable")
def admin_disable_sip_number(trunk_id: int, request: Request, db: Session = Depends(get_db)):
    get_current_super_admin(request, db)

    trunk = db.query(SIPTrunk).filter(SIPTrunk.id == trunk_id).first()

    if not trunk:
        raise HTTPException(status_code=404, detail="SIP trunk not found")

    active_calls = get_trunk_active_call_count(db, trunk.id)

    if active_calls > 0:
        raise HTTPException(
            status_code=409,
            detail=(
                f"SIP number {trunk.number} still has {active_calls} call(s) in progress. "
                f"Wait for them to finish, or cancel the running campaign first."
            ),
        )

    cols = model_columns(SIPTrunk)

    previous_is_active = trunk.is_active
    previous_is_applied = getattr(trunk, "is_applied", None)

    trunk.is_active = False

    if "is_applied" in cols:
        trunk.is_applied = False

    db.commit()

    generate_result, apply_result = regenerate_and_apply(db)

    if apply_result["ok"]:
        return RedirectResponse(url="/admin/sip-numbers", status_code=303)

    # Asterisk was never reloaded, so revert the DB to match what is actually
    # live. Leaving it disabled here would silently desync CRM from Asterisk.
    trunk.is_active = previous_is_active

    if "is_applied" in cols:
        trunk.is_applied = previous_is_applied

    if "last_apply_error" in cols:
        trunk.last_apply_error = (
            apply_result.get("stderr")
            or apply_result.get("stdout")
            or "Unknown disable apply error"
        )

    db.commit()

    # Put the generated file back in sync with the reverted DB state.
    generate_pjsip_config(get_active_managed_trunks(db))

    raise apply_failed_error(
        "Could not apply to Asterisk. The SIP number was left enabled.",
        generate_result,
        apply_result,
    )

@router.get("/sip-trunks")
def admin_sip_trunks_alias(
    request: Request,
    db: Session = Depends(get_db),
):
    get_current_super_admin(request, db)

    return RedirectResponse(
        url="/admin/sip-numbers",
        status_code=303,
    )