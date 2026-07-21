# app/tasks/campaign_tasks.py
import time
from datetime import datetime, timezone

from sqlalchemy import or_, func, case

from app.celery_app import celery_app
from app.database import SessionLocal
from app.models.campaign import Campaign, CampaignStatus
from app.models.contact import Contact
from app.models.sip_trunk import SIPTrunk
from app.models.call_log import CallLog, CallStatus
from app.services.asterisk import AsteriskService
from app.services.asterisk_status import get_pjsip_registration_status
from app.services.call_signal import wait_call_done_signal

from app.models.campaign_target import CampaignTarget   

FINAL_CALL_STATUSES = {
    CallStatus.completed,
    CallStatus.failed,
    CallStatus.busy,
    CallStatus.no_answer,
    CallStatus.congestion,
}


def wait_until_call_finished(db, call_log_id: int, max_wait_sec: int = 120):
    # First fresh DB check. Maybe listener already completed before we started waiting.
    db.expire_all()

    call = (
        db.query(CallLog)
        .filter(CallLog.id == call_log_id)
        .populate_existing()
        .first()
    )

    if not call:
        return None

    if call.status in FINAL_CALL_STATUSES:
        return call

    # Wait for listener signal from Redis.
    got_signal = wait_call_done_signal(
        call_log_id=call_log_id,
        timeout_sec=int(max_wait_sec),
    )

    # Fresh DB read after signal or timeout.
    db.expire_all()

    call = (
        db.query(CallLog)
        .filter(CallLog.id == call_log_id)
        .populate_existing()
        .first()
    )

    if not call:
        return None

    if call.status in FINAL_CALL_STATUSES:
        return call

    # Safety fallback only if Redis signal was missed and DB still says calling.
    if not got_signal and call.status == CallStatus.calling:
        call.status = CallStatus.congestion
        call.ended_at = datetime.now(timezone.utc)
        call.duration_sec = 0
        db.commit()
        db.refresh(call)

    return call




def old_system_cooldown(call):
    if not call:
        time.sleep(5)
        return

    if call.status == CallStatus.completed:
        # Hangup already confirmed call is 100% done
        # just a small carrier teardown buffer
        sleep_sec = 10.0

    elif call.status == CallStatus.congestion:
        # trunk is overloaded — give it a real rest
        sleep_sec = 45.0

    elif call.status == CallStatus.no_answer:
        # subscriber didn't answer — short wait
        sleep_sec = 10.0

    elif call.status == CallStatus.busy:
        # line was busy — short wait
        sleep_sec = 10.0

    else:
        # failed, unknown
        sleep_sec = 10.0

    print(
        f"Celery: cooldown. "
        f"phone={call.phone} status={call.status} sleep={sleep_sec:.1f}s"
    )
    time.sleep(sleep_sec)

SLOT_WAIT_SEC = 1.0
DEFAULT_STUCK_CALL_TIMEOUT_SEC = 120.0
STUCK_CALL_EXTRA_SEC = 30.0
MIN_STUCK_CALL_TIMEOUT_SEC = 45.0

FINAL_STATUSES = {
    CallStatus.completed,
    CallStatus.failed,
    CallStatus.busy,
    CallStatus.no_answer,
    CallStatus.congestion,
}


def utc_now():
    return datetime.now(timezone.utc)


def as_utc(value):
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def refresh_campaign_stats(db, campaign: Campaign):
    stats = db.query(
        func.count(CallLog.id).label("total_calls"),
        func.sum(case((CallLog.status == CallStatus.completed, 1), else_=0)).label("completed_calls"),
        func.sum(case((CallLog.status == CallStatus.busy, 1), else_=0)).label("busy_calls"),
        func.sum(case((CallLog.status == CallStatus.no_answer, 1), else_=0)).label("no_answer_calls"),
        func.sum(case((CallLog.status == CallStatus.failed, 1), else_=0)).label("failed_only_calls"),
        func.sum(case((CallLog.status == CallStatus.congestion, 1), else_=0)).label("congestion_calls"),
    ).filter(
        CallLog.campaign_id == campaign.id
    ).first()

    total_calls = int(stats.total_calls or 0)
    completed_calls = int(stats.completed_calls or 0)
    busy_calls = int(stats.busy_calls or 0)
    no_answer_calls = int(stats.no_answer_calls or 0)
    failed_only_calls = int(stats.failed_only_calls or 0)
    congestion_calls = int(stats.congestion_calls or 0)

    campaign.completed_calls = completed_calls
    campaign.busy_calls = busy_calls
    campaign.no_answer_calls = no_answer_calls
    campaign.failed_calls = failed_only_calls + congestion_calls

    if not campaign.total_contacts:
        target_contact_ids = campaign.target_contact_ids or []
        campaign.total_contacts = len(target_contact_ids) or total_calls

    finished_calls = (
        completed_calls
        + busy_calls
        + no_answer_calls
        + failed_only_calls
        + congestion_calls
    )

    total_contacts = campaign.total_contacts or total_calls

    if (
        campaign.status != CampaignStatus.cancelled
        and total_contacts > 0
        and finished_calls >= total_contacts
    ):
        campaign.status = CampaignStatus.completed
        if not campaign.completed_at:
            campaign.completed_at = utc_now()


def get_stuck_call_timeout(campaign: Campaign) -> float:
    audio_duration = None

    try:
        if campaign.audio_file and campaign.audio_file.duration_sec:
            audio_duration = float(campaign.audio_file.duration_sec)
    except Exception:
        audio_duration = None

    if audio_duration and audio_duration > 0:
        return max(MIN_STUCK_CALL_TIMEOUT_SEC, audio_duration + STUCK_CALL_EXTRA_SEC)

    return DEFAULT_STUCK_CALL_TIMEOUT_SEC


def get_active_call_count(db, campaign_id: int) -> int:
    return db.query(CallLog).filter(
        CallLog.campaign_id == campaign_id,
        CallLog.status == CallStatus.calling,
    ).count()


def get_trunk_active_call_count(db, trunk_id: int) -> int:
    return db.query(CallLog).filter(
        CallLog.trunk_id == trunk_id,
        CallLog.status == CallStatus.calling,
    ).count()


def get_registered_allowed_trunks(db, company_id: int) -> list[SIPTrunk]:
    """
    Return SIP trunks this company can use.

    Rules:
        - active
        - applied
        - Registered in Asterisk
        - assigned_company_id is NULL or same company
    """
    registration_statuses = get_pjsip_registration_status()

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

    trunks = query.order_by(SIPTrunk.id.asc()).all()

    registered_trunks = []

    for trunk in trunks:
        endpoint = str(trunk.asterisk_endpoint or "").strip()

        if not endpoint:
            continue

        reg_status = registration_statuses.get(endpoint, "Unknown")

        if reg_status != "Registered":
            print(
                f"SIP trunk skipped: number={trunk.number}, "
                f"endpoint={endpoint}, register_status={reg_status}"
            )
            continue

        registered_trunks.append(trunk)

    return registered_trunks


def get_available_sip_trunk(db, company_id: int, selected_sip_trunk_id: int | None = None):
    """
    Select one available SIP trunk without conflict.

    Available means:
        active + applied + registered + not full
    """
    trunks = get_registered_allowed_trunks(db, company_id)

    if selected_sip_trunk_id:
        trunks = [
            trunk for trunk in trunks
            if trunk.id == selected_sip_trunk_id
        ]   

        if not trunks:
            print(
                f"SIP selected by campaign is not registered/allowed: "
                f"selected_sip_trunk_id={selected_sip_trunk_id}"
            )
            return None

    for trunk in trunks:
        max_concurrent = max(1, int(trunk.max_concurrent or 1))
        active_count = get_trunk_active_call_count(db, trunk.id)

        if active_count < max_concurrent:
            print(
                f"SIP selected: number={trunk.number}, "
                f"endpoint={trunk.asterisk_endpoint}, "
                f"active={active_count}/{max_concurrent}"
            )
            return trunk

        print(
            f"SIP full: number={trunk.number}, "
            f"endpoint={trunk.asterisk_endpoint}, "
            f"active={active_count}/{max_concurrent}"
        )

    return None


def mark_stuck_calling(db, campaign: Campaign, timeout_sec: float) -> int:

    db.expire_all()
    now = utc_now()
    stuck_count = 0

    calling_calls = db.query(CallLog).filter(
        CallLog.campaign_id == campaign.id,
        CallLog.status == CallStatus.calling,
    ).all()

    for call in calling_calls:
        started_at = as_utc(call.started_at)

        if not started_at:
            continue

        if (now - started_at).total_seconds() <= timeout_sec:
            continue

        call.status = CallStatus.congestion
        call.ended_at = now

        if call.duration_sec is None:
            call.duration_sec = 0

        stuck_count += 1

    if stuck_count:
        db.flush()
        print(
            f"Celery: Marked {stuck_count} stuck calling call(s) as congestion "
            f"for campaign {campaign.id}."
        )

    return stuck_count


def get_campaign_target_contacts(db, campaign: Campaign) -> list[Contact]:
    target_rows = db.query(CampaignTarget).filter(
        CampaignTarget.campaign_id == campaign.id,
        CampaignTarget.status.in_(["pending", "calling"]),
    ).order_by(
        CampaignTarget.position.asc(),
        CampaignTarget.id.asc(),
    ).all()

    if target_rows:
        contact_ids = [
            row.contact_id
            for row in target_rows
            if row.contact_id is not None
        ]

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

        for row in target_rows:
            contact = contacts_by_id.get(row.contact_id)

            if contact:
                ordered_contacts.append(contact)

        return ordered_contacts

    # Fallback for older campaigns without campaign_targets
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
                f"Celery fallback: duplicate target phone skipped. "
                f"campaign_id={campaign.id}, phone={normalized_phone}, contact_id={contact.id}"
            )
            continue

        seen_phones.add(normalized_phone)
        deduped_contacts.append(contact)

    return deduped_contacts


def campaign_cancelled(db, campaign: Campaign) -> bool:
    db.refresh(campaign)
    return campaign.status == CampaignStatus.cancelled


def wait_for_available_sip_trunk(
    db,
    campaign: Campaign,
    stuck_timeout_sec: float,
    selected_sip_trunk_id: int | None = None,
):
    """
    Wait until any allowed SIP trunk has free capacity.
    This prevents companies/users from conflicting on the same SIP number.
    """
    while True:
        if campaign_cancelled(db, campaign):
            return None

        if mark_stuck_calling(db, campaign, stuck_timeout_sec):
            refresh_campaign_stats(db, campaign)
            db.commit()
            db.refresh(campaign)

            if campaign.status == CampaignStatus.cancelled:
                return None

        trunk = get_available_sip_trunk(
            db=db,
            company_id=campaign.company_id,
            selected_sip_trunk_id=selected_sip_trunk_id,
        )

        if trunk:
            return trunk

        time.sleep(SLOT_WAIT_SEC)


def wait_for_active_calls_to_finish(db, campaign: Campaign, stuck_timeout_sec: float) -> bool:
    while True:
        if campaign_cancelled(db, campaign):
            return False

        if mark_stuck_calling(db, campaign, stuck_timeout_sec):
            refresh_campaign_stats(db, campaign)
            db.commit()
            db.refresh(campaign)

            if campaign.status == CampaignStatus.cancelled:
                return False

        active_count = get_active_call_count(db, campaign.id)

        if active_count == 0:
            return True

        time.sleep(SLOT_WAIT_SEC)


def call_log_exists(db, campaign_id: int, contact_id: int) -> bool:
    db.expire_all()

    return db.query(CallLog.id).filter(
        CallLog.campaign_id == campaign_id,
        CallLog.contact_id == contact_id,
    ).first() is not None

def get_campaign_target_count(db, campaign: Campaign) -> int:
    target_count = db.query(func.count(CampaignTarget.id)).filter(
        CampaignTarget.campaign_id == campaign.id,
    ).scalar() or 0

    if target_count > 0:
        return int(target_count)

    return len(campaign.target_contact_ids or [])

@celery_app.task(bind=True, name="run_campaign_task")
def run_campaign_task(self, campaign_id: int):
    """Execute one campaign using registered, available SIP trunks."""
    db = SessionLocal()

    try:
        campaign = db.query(Campaign).filter(Campaign.id == campaign_id).first()

        if not campaign:
            print(f"Celery: Campaign {campaign_id} not found.")
            return

        # Production safety:
        # Atomically claim this campaign.
        # Only one worker can change draft -> running.
        # Duplicate Celery tasks will exit here.
        claimed = db.query(Campaign).filter(
            Campaign.id == campaign_id,
            Campaign.status.in_([
                CampaignStatus.draft,
                CampaignStatus.queued,
            ]),
        ).update(
            {
                Campaign.status: CampaignStatus.running,
                Campaign.started_at: utc_now(),
                Campaign.completed_at: None,
            },
            synchronize_session=False,
        )

        db.commit()

        if claimed != 1:
            db.expire_all()

            campaign = db.query(Campaign).filter(
                Campaign.id == campaign_id
            ).first()

            print(
                f"Celery: Campaign {campaign_id} was not claimed. "
                f"Current status={campaign.status if campaign else None}. "
                f"Another worker may already be running it."
            )
            return

        db.expire_all()

        campaign = db.query(Campaign).filter(
            Campaign.id == campaign_id
        ).first()

        if not campaign:
            print(f"Celery: Campaign {campaign_id} disappeared after claim.")
            return

        selected_sip_trunk_id = getattr(campaign, "selected_sip_trunk_id", None)
        target_count = get_campaign_target_count(db, campaign)

        if target_count <= 0:
            print(
                f"Celery: Campaign {campaign_id} has no campaign targets. "
                f"Refusing to run."
            )
            campaign.status = CampaignStatus.failed
            db.commit()
            return

        contacts = get_campaign_target_contacts(db, campaign)

        if not contacts:
            campaign.status = CampaignStatus.failed
            campaign.completed_at = utc_now()
            db.commit()
            print(f"Celery: Campaign {campaign_id} has no active frozen target contacts.")
            return

        registered_trunks = get_registered_allowed_trunks(db, campaign.company_id)

        if not registered_trunks:
            campaign.status = CampaignStatus.failed
            campaign.completed_at = utc_now()
            db.commit()
            print(
                f"Celery: Campaign {campaign_id} failed. "
                f"No Registered SIP trunk available for company_id={campaign.company_id}."
            )
            return

        total_capacity = sum(
            max(1, int(trunk.max_concurrent or 1))
            for trunk in registered_trunks
        )

        campaign.total_contacts = len(contacts)

        refresh_campaign_stats(db, campaign)
        db.commit()

        triggered_calls = 0
        skipped_duplicates = 0
        stuck_timeout_sec = get_stuck_call_timeout(campaign)

        print(
            f"Celery: Campaign {campaign_id} starting with {len(contacts)} contacts, "
            f"sip_capacity={total_capacity}, stuck_timeout={stuck_timeout_sec:.0f}s."
        )

        for contact in contacts:
            if campaign_cancelled(db, campaign):
                print(f"Celery: Campaign {campaign.id} cancelled. Stopping before next call.")
                break
            db.refresh(campaign)

            if campaign.status == CampaignStatus.cancelled:
                print(f"Celery: Campaign {campaign_id} cancelled before next originate.")
                break

            if call_log_exists(db, campaign.id, contact.id):
                skipped_duplicates += 1
                continue

            trunk = wait_for_available_sip_trunk(
                db=db,
                campaign=campaign,
                stuck_timeout_sec=stuck_timeout_sec,
                selected_sip_trunk_id=selected_sip_trunk_id,
            )

            if not trunk:
                print(f"Celery: Campaign {campaign_id} stopped while waiting for SIP trunk.")
                break

            db.refresh(campaign)

            if campaign.status == CampaignStatus.cancelled:
                print(f"Celery: Campaign {campaign_id} cancelled before originate.")
                break

            if call_log_exists(db, campaign.id, contact.id):
                skipped_duplicates += 1
                continue

            try:
                call_log = AsteriskService.initiate_call(
                    db=db,
                    campaign_id=campaign.id,
                    contact_id=contact.id,
                    phone_number=contact.phone,
                    trunk_id=trunk.id,
                    audio_filename=campaign.audio_file.filename,
                )

                triggered_calls += 1

                print(
                    f"Celery: Submitted call. phone={contact.phone}, "
                    f"call_log_id={call_log.id}, status={call_log.status}"
                )

                finished_call = wait_until_call_finished(
                    db=db,
                    call_log_id=call_log.id,
                    max_wait_sec=stuck_timeout_sec,
                )

                if finished_call:
                    print(
                        f"Celery: Call finished. phone={finished_call.phone}, "
                        f"status={finished_call.status}, "
                        f"duration={finished_call.duration_sec}, "
                        f"cause={finished_call.hangup_cause}"
                    )

                refresh_campaign_stats(db, campaign)
                db.commit()

                old_system_cooldown(finished_call)

                refresh_campaign_stats(db, campaign)
                db.commit()

                print(
                    f"Celery: Originated {contact.phone} "
                    f"via SIP {trunk.number}/{trunk.asterisk_endpoint}"
                )

            except Exception as e:
                db.rollback()

                campaign = db.query(Campaign).filter(Campaign.id == campaign_id).first()

                if not campaign or campaign.status == CampaignStatus.cancelled:
                    break

                print(f"Celery error triggering call for {contact.phone}: {e}")
                time.sleep(SLOT_WAIT_SEC)

        db.refresh(campaign)

        if campaign.status != CampaignStatus.cancelled:
            wait_for_active_calls_to_finish(db, campaign, stuck_timeout_sec)

        db.refresh(campaign)

        if campaign.status != CampaignStatus.cancelled:
            refresh_campaign_stats(db, campaign)
            campaign.status = CampaignStatus.completed
            campaign.completed_at = utc_now()
            db.commit()
        else:
            db.commit()
            print(f"Celery: Campaign {campaign.id} remained cancelled.")

        print(
            f"Celery: Campaign {campaign_id} finished. "
            f"originated={triggered_calls}, skipped_duplicates={skipped_duplicates}."
        )

    except Exception as e:
        db.rollback()
        print(f"Celery: Critical error during campaign task: {e}")

        campaign = db.query(Campaign).filter(Campaign.id == campaign_id).first()

        if campaign and campaign.status != CampaignStatus.cancelled:
            campaign.status = CampaignStatus.failed
            campaign.completed_at = utc_now()
            db.commit()

    finally:
        db.close()  