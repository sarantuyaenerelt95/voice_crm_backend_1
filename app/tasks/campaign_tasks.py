# app/tasks/campaign_tasks.py

from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Optional, List
import subprocess

from sqlalchemy import or_, func, case

from app.celery_app import celery_app
from app.database import SessionLocal
from app.models.campaign import Campaign, CampaignStatus
from app.models.contact import Contact
from app.models.sip_trunk import SIPTrunk
from app.models.call_log import CallLog, CallStatus
from app.models.campaign_target import CampaignTarget
from app.services.asterisk import AsteriskService, ORIGINATE_TIMEOUT_SEC
from app.services.asterisk_status import get_pjsip_registration_status


SLOT_WAIT_SEC = 1.0
DEFAULT_STUCK_CALL_TIMEOUT_SEC = 120.0
DIAL_TIMEOUT_SEC = 60.0
STUCK_CALL_EXTRA_SEC = 90.0

# The sweeper must outlive Asterisk's own originate timeout, otherwise it marks
# a still-ringing call as congestion and frees the slot too early.
MIN_STUCK_CALL_TIMEOUT_SEC = ORIGINATE_TIMEOUT_SEC + 40.0

# Abort a campaign that cannot make any forward progress (no originate, no call
# finishing) for this long. Without this the scheduler loop spins forever and
# permanently occupies a Celery worker.
MAX_NO_PROGRESS_SEC = 900.0

# How many times one contact may fail to originate before we give up on it
# instead of pushing it back onto the queue forever.
MAX_CONTACT_ATTEMPTS = 3

# Re-read trunk registration/capacity while a campaign runs, so enabling or
# disabling a trunk mid-campaign is picked up.
TRUNK_REFRESH_SEC = 15.0

# Cache window for the live Asterisk channel count (one CLI call, not one per trunk).
LIVE_CHANNEL_CACHE_SEC = 0.9

SLOT_RELEASE_COMPLETED_SEC = 3.0
SLOT_RELEASE_BUSY_SEC = 5.0
SLOT_RELEASE_NO_ANSWER_SEC = 5.0
SLOT_RELEASE_RTP_TIMEOUT_SEC = 8.0
SLOT_RELEASE_CONGESTION_SEC = 45.0
SLOT_RELEASE_FAILED_SEC = 20.0

ORIGINATE_SPACING_SEC = 2.0
CONGESTION_BACKOFF_SEC = 45.0

FINAL_CALL_STATUSES = {
    CallStatus.completed,
    CallStatus.failed,
    CallStatus.busy,
    CallStatus.no_answer,
    CallStatus.congestion,
}

FINAL_STATUSES = FINAL_CALL_STATUSES


def utc_now():
    return datetime.now(timezone.utc)


def as_utc(value):
    if value is None:
        return None

    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)

    return value


def normalize_phone(phone: Optional[str]) -> str:
    return "".join(
        ch for ch in str(phone or "").strip()
        if ch.isdigit()
    )


def call_status_value(status) -> str:
    if hasattr(status, "value"):
        return str(status.value)

    value = str(status or "").lower().strip()

    if "." in value:
        value = value.split(".")[-1]

    return value


def update_campaign_target_from_call(db, call: Optional[CallLog]) -> None:
    if not call:
        return

    status_value = call_status_value(call.status)

    if not status_value:
        return

    target = db.query(CampaignTarget).filter(
        CampaignTarget.campaign_id == call.campaign_id,
        CampaignTarget.call_log_id == call.id,
    ).first()

    if not target:
        target = db.query(CampaignTarget).filter(
            CampaignTarget.campaign_id == call.campaign_id,
            CampaignTarget.phone == normalize_phone(call.phone),
        ).first()

    if not target:
        return

    target.status = status_value
    target.call_log_id = call.id
    target.updated_at = utc_now()


def refresh_campaign_stats(db, campaign: Campaign):
    stats = db.query(
        func.count(CallLog.id).label("total_calls"),
        func.sum(case((CallLog.status == CallStatus.completed, 1), else_=0)).label("completed_calls"),
        func.sum(case((CallLog.status == CallStatus.busy, 1), else_=0)).label("busy_calls"),
        func.sum(case((CallLog.status == CallStatus.no_answer, 1), else_=0)).label("no_answer_calls"),
        func.sum(case((CallLog.status == CallStatus.failed, 1), else_=0)).label("failed_only_calls"),
        func.sum(case((CallLog.status == CallStatus.congestion, 1), else_=0)).label("congestion_calls"),
    ).filter(
        CallLog.campaign_id == campaign.id,
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

    audio_sec = audio_duration if audio_duration and audio_duration > 0 else 30.0

    # Ring time + audio playback + safety buffer.
    # Some Mobinet calls answer after 50–60 seconds, so 45s is too short.
    timeout_sec = DIAL_TIMEOUT_SEC + audio_sec + STUCK_CALL_EXTRA_SEC

    return max(MIN_STUCK_CALL_TIMEOUT_SEC, timeout_sec)


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


def get_registered_allowed_trunks(db, company_id: int) -> List[SIPTrunk]:
    registration_statuses = get_pjsip_registration_status()

    query = db.query(SIPTrunk).filter(
        SIPTrunk.is_active == True,
    )

    if hasattr(SIPTrunk, "is_applied"):
        query = query.filter(
            SIPTrunk.is_applied == True,
        )

    if hasattr(SIPTrunk, "assigned_company_id"):
        query = query.filter(
            or_(
                SIPTrunk.assigned_company_id == None,
                SIPTrunk.assigned_company_id == company_id,
            )
        )

    trunks = query.order_by(
        SIPTrunk.id.asc(),
    ).all()

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


def get_available_sip_trunk(
    db,
    company_id: int,
    selected_sip_trunk_id: Optional[int] = None,
):
    trunks = get_registered_allowed_trunks(db, company_id)

    if selected_sip_trunk_id:
        trunks = [
            trunk for trunk in trunks
            if int(trunk.id) == int(selected_sip_trunk_id)
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

        # A call with no start time cannot be aged out, so it would otherwise
        # stay in "calling" forever and block the campaign from ever finishing.
        if started_at and (now - started_at).total_seconds() <= timeout_sec:
            continue

        call.status = CallStatus.congestion
        call.ended_at = now

        if call.duration_sec is None:
            call.duration_sec = 0

        update_campaign_target_from_call(db, call)
        stuck_count += 1

    if stuck_count:
        db.flush()

        print(
            f"Celery: Marked {stuck_count} stuck calling call(s) as congestion "
            f"for campaign {campaign.id}."
        )

    return stuck_count


def get_campaign_target_contacts(db, campaign: Campaign) -> List[Contact]:
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

    target_contact_ids = campaign.target_contact_ids or []

    if not target_contact_ids:
        return []

    contacts = db.query(Contact).filter(
        Contact.company_id == campaign.company_id,
        Contact.is_active == True,
        Contact.id.in_(target_contact_ids),
    ).all()

    contacts_by_id = {
        contact.id: contact
        for contact in contacts
    }

    deduped_contacts = []
    seen_phones = set()

    for contact_id in target_contact_ids:
        contact = contacts_by_id.get(contact_id)

        if not contact:
            continue

        normalized_phone = normalize_phone(contact.phone)

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


def wait_for_active_calls_to_finish(db, campaign: Campaign, stuck_timeout_sec: float) -> bool:
    # Hard backstop: the sweeper should retire every call within stuck_timeout,
    # so anything beyond that plus a margin means we are not converging. Give up
    # rather than hold the Celery worker forever.
    deadline = time.monotonic() + stuck_timeout_sec + MAX_NO_PROGRESS_SEC

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

        if time.monotonic() > deadline:
            print(
                f"Celery: Campaign {campaign.id} still has {active_count} call(s) "
                f"in 'calling' after the drain deadline. Giving up the wait."
            )
            return False

        time.sleep(SLOT_WAIT_SEC)


def get_existing_call_log(db, campaign_id: int, contact_id: int, phone: str = ""):
    """Find an already-created call log for this campaign.

    AsteriskService.initiate_call de-duplicates on (campaign_id, phone), so we
    must check the same key here. Otherwise two contacts sharing a phone number
    look "new" to the scheduler, and initiate_call silently hands back the
    earlier (often already finished) call log.
    """
    db.expire_all()

    normalized_phone = normalize_phone(phone)

    query = db.query(CallLog).filter(
        CallLog.campaign_id == campaign_id,
    )

    if normalized_phone:
        query = query.filter(
            or_(
                CallLog.contact_id == contact_id,
                CallLog.phone == normalized_phone,
            )
        )
    else:
        query = query.filter(CallLog.contact_id == contact_id)

    return query.first()


def get_campaign_target_count(db, campaign: Campaign) -> int:
    target_count = db.query(func.count(CampaignTarget.id)).filter(
        CampaignTarget.campaign_id == campaign.id,
    ).scalar() or 0

    if target_count > 0:
        return int(target_count)

    return len(campaign.target_contact_ids or [])


def mark_pending_targets_cancelled(db, campaign: Campaign) -> int:
    now = utc_now()

    cancelled_targets = db.query(CampaignTarget).filter(
        CampaignTarget.campaign_id == campaign.id,
        CampaignTarget.status.in_(["pending", "calling"]),
        CampaignTarget.call_log_id == None,
    ).update(
        {
            CampaignTarget.status: "cancelled",
            CampaignTarget.updated_at: now,
        },
        synchronize_session=False,
    )

    return int(cancelled_targets or 0)

_live_channels_cache = {"at": 0.0, "lines": []}


def get_live_channel_lines() -> List[str]:
    """Read Asterisk's channel list once and cache it briefly.

    The scheduler polls every second, so without this cache we spawned one
    `asterisk -rx` subprocess per trunk per iteration.
    """
    now = time.monotonic()

    if now - _live_channels_cache["at"] < LIVE_CHANNEL_CACHE_SEC:
        return _live_channels_cache["lines"]

    try:
        result = subprocess.run(
            ["sudo", "-n", "/usr/sbin/asterisk", "-rx", "core show channels concise"],
            text=True,
            capture_output=True,
            timeout=3,
        )

        lines = ((result.stdout or "") + (result.stderr or "")).splitlines()

    except Exception as e:
        print(f"Celery: live Asterisk channel count failed: {e}")
        lines = []

    _live_channels_cache["at"] = now
    _live_channels_cache["lines"] = lines

    return lines


def get_live_endpoint_channel_count(endpoint: str) -> int:
    if not endpoint:
        return 0

    endpoint_prefix = f"PJSIP/{endpoint}-"

    return sum(
        1
        for line in get_live_channel_lines()
        if endpoint_prefix in line
    )

def to_call_log_id(value):
    if value is None:
        return None

    if hasattr(value, "id"):
        return int(value.id)

    return int(value)

def safe_int(value, default=0):
    try:
        if value is None:
            return default
        return int(value)
    except Exception:
        return default

def is_provider_congestion_call(call) -> bool:
    if not call:
        return False

    cause_code = safe_int(getattr(call, "hangup_cause", None))

    return cause_code in (34, 38, 41, 42, 50)


def cleanup_slot_cooldowns(slot_cooldowns):
    now = time.monotonic()
    return [
        release_at
        for release_at in slot_cooldowns
        if release_at > now
    ]


def get_live_registered_channel_count(registered_trunks) -> int:
    total = 0

    for trunk in registered_trunks:
        endpoint = str(trunk.asterisk_endpoint or "").strip()

        if not endpoint:
            continue

        total += get_live_endpoint_channel_count(endpoint)

    return total


def get_slot_release_delay_sec(call) -> float:
    if not call:
        return SLOT_RELEASE_FAILED_SEC

    status_text = call_status_value(call.status)
    cause_code = safe_int(getattr(call, "hangup_cause", None))

    # Normal completed broadcast call.
    # Small delay lets Mobinet release channel cleanly.
    if status_text == "completed":
        return SLOT_RELEASE_COMPLETED_SEC

    # RTP timeout.
    # Keep same idea as your RTP logic.
    if cause_code == 44:
        return SLOT_RELEASE_RTP_TIMEOUT_SEC

    # Provider/network congestion.
    # Important: do not reuse this slot immediately.
    if cause_code in (34, 38, 41, 42, 50):
        return SLOT_RELEASE_CONGESTION_SEC

    if status_text == "busy":
        return SLOT_RELEASE_BUSY_SEC

    if status_text == "no_answer":
        return SLOT_RELEASE_NO_ANSWER_SEC

    return SLOT_RELEASE_FAILED_SEC


def call_is_final(call) -> bool:
    if not call:
        return True

    status = call.status

    if hasattr(status, "value"):
        status_text = str(status.value).lower().strip()
    else:
        status_text = str(status or "").lower().strip()

    if "." in status_text:
        status_text = status_text.split(".")[-1]

    final_statuses = {
        "completed",
        "busy",
        "no_answer",
        "failed",
        "congestion",
        "cancelled",
    }

    return call.ended_at is not None or status_text in final_statuses



@celery_app.task(bind=True, name="run_campaign_task")
def run_campaign_task(self, campaign_id: int):
    """Execute one campaign using registered, available SIP trunks."""
    db = SessionLocal()

    try:
        campaign = db.query(Campaign).filter(
            Campaign.id == campaign_id,
        ).first()

        if not campaign:
            print(f"Celery: Campaign {campaign_id} not found.")
            return

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
                Campaign.id == campaign_id,
            ).first()

            print(
                f"Celery: Campaign {campaign_id} was not claimed. "
                f"Current status={campaign.status if campaign else None}. "
                f"Another worker may already be running it."
            )

            return

        db.expire_all()

        campaign = db.query(Campaign).filter(
            Campaign.id == campaign_id,
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
            campaign.completed_at = utc_now()
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

        if selected_sip_trunk_id:
            registered_trunks = [
                trunk for trunk in registered_trunks
                if int(trunk.id) == int(selected_sip_trunk_id)
            ]

        if not registered_trunks:
            campaign.status = CampaignStatus.failed
            campaign.completed_at = utc_now()
            db.commit()

            print(
                f"Celery: Campaign {campaign_id} failed. "
                f"No Registered SIP trunk available for company_id={campaign.company_id}, "
                f"selected_sip_trunk_id={selected_sip_trunk_id}."
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

        pending_contacts = list(contacts)
        active_call_ids = set()
        slot_cooldowns = []
        last_originate_at = 0.0
        congestion_backoff_until = 0.0

        last_progress_at = time.monotonic()
        last_trunk_refresh_at = time.monotonic()
        contact_attempts = {}
        abandoned_contacts = 0

        print(
            f"Celery: slot scheduler enabled. "
            f"campaign_id={campaign_id}, total_contacts={len(pending_contacts)}, "
            f"sip_capacity={total_capacity}"
        )

        while pending_contacts or active_call_ids:
            if campaign_cancelled(db, campaign):
                print(f"Celery: Campaign {campaign.id} cancelled. Slot scheduler stopping.")
                break

            db.expire_all()

            campaign = db.query(Campaign).filter(
                Campaign.id == campaign_id,
            ).first()

            if not campaign:
                print(f"Celery: Campaign {campaign_id} disappeared.")
                break

            if campaign.status == CampaignStatus.cancelled:
                print(f"Celery: Campaign {campaign_id} cancelled.")
                break

            # Give up if nothing can move forward any more. Without this the
            # loop spins at SLOT_WAIT_SEC forever (e.g. the selected trunk
            # deregistered) and holds this Celery worker permanently.
            stalled_for = time.monotonic() - last_progress_at

            if stalled_for > MAX_NO_PROGRESS_SEC:
                print(
                    f"Celery: Campaign {campaign_id} made no progress for "
                    f"{stalled_for:.0f}s (pending={len(pending_contacts)}, "
                    f"active={len(active_call_ids)}). Aborting to free the worker."
                )
                break

            # Pick up trunks that were enabled/disabled while this campaign runs.
            if time.monotonic() - last_trunk_refresh_at > TRUNK_REFRESH_SEC:
                last_trunk_refresh_at = time.monotonic()

                refreshed_trunks = get_registered_allowed_trunks(db, campaign.company_id)

                if selected_sip_trunk_id:
                    refreshed_trunks = [
                        trunk for trunk in refreshed_trunks
                        if int(trunk.id) == int(selected_sip_trunk_id)
                    ]

                if refreshed_trunks:
                    refreshed_capacity = sum(
                        max(1, int(trunk.max_concurrent or 1))
                        for trunk in refreshed_trunks
                    )

                    if refreshed_capacity != total_capacity:
                        print(
                            f"Celery: SIP capacity changed mid-campaign. "
                            f"{total_capacity} -> {refreshed_capacity}"
                        )

                    registered_trunks = refreshed_trunks
                    total_capacity = refreshed_capacity

            if mark_stuck_calling(db, campaign, stuck_timeout_sec):
                refresh_campaign_stats(db, campaign)
                db.commit()
                db.expire_all()

            # 1) Remove finished calls from active slots.
            # Finished calls do not free the physical provider slot immediately.
            # We add a short cooldown slot to avoid Mobinet rapid-fire congestion.
            slot_cooldowns = cleanup_slot_cooldowns(slot_cooldowns)

            for active_item in list(active_call_ids):
                call_id = to_call_log_id(active_item)

                if call_id is None:
                    active_call_ids.discard(active_item)
                    continue

                finished_call = db.query(CallLog).filter(
                    CallLog.id == call_id,
                ).first()

                if call_is_final(finished_call):
                    active_call_ids.discard(active_item)
                    last_progress_at = time.monotonic()

                    if finished_call:
                        update_campaign_target_from_call(db, finished_call)

                        release_delay = get_slot_release_delay_sec(finished_call)

                        if release_delay > 0:
                            slot_cooldowns.append(
                                time.monotonic() + release_delay
                            )
                        if is_provider_congestion_call(finished_call):
                            congestion_backoff_until = max(
                                congestion_backoff_until,
                                time.monotonic() + CONGESTION_BACKOFF_SEC,
                            )

                            print(
                                f"Celery: Provider congestion backoff activated. "
                                f"phone={finished_call.phone}, "
                                f"cause={finished_call.hangup_cause}, "
                                f"backoff={CONGESTION_BACKOFF_SEC:.1f}s"
                            )

                        print(
                            f"Celery: Slot freed with release delay. "
                            f"phone={finished_call.phone}, "
                            f"call_log_id={finished_call.id}, "
                            f"status={finished_call.status}, "
                            f"duration={finished_call.duration_sec}, "
                            f"cause={finished_call.hangup_cause}, "
                            f"release_delay={release_delay:.1f}s"
                        )

            refresh_campaign_stats(db, campaign)
            db.commit()

            # 2) Fill free slots up to total_capacity.
            # Example: total_capacity=4
            # It will submit call 1, 2, 3, 4 immediately.
            # Then it waits until one active call finishes.
            while pending_contacts:
                slot_cooldowns = cleanup_slot_cooldowns(slot_cooldowns)

                live_count = get_live_registered_channel_count(registered_trunks)
                used_slots = min(
                    total_capacity,
                    max(len(active_call_ids), live_count) + len(slot_cooldowns)
                )   

                if used_slots >= total_capacity:
                    print(
                        f"Celery: Slots full. "
                        f"db_active={len(active_call_ids)}, "
                        f"live={live_count}, "
                        f"cooldown={len(slot_cooldowns)}, "
                        f"used={used_slots}/{total_capacity}. Waiting..."
                    )
                    break

                now_mono = time.monotonic()

                if congestion_backoff_until > now_mono:
                    remaining = congestion_backoff_until - now_mono

                    print(
                        f"Celery: Provider congestion backoff active. "
                        f"remaining={remaining:.1f}s. Waiting..."
                    )

                    break

                if last_originate_at > 0:
                    since_last_originate = now_mono - last_originate_at

                    if since_last_originate < ORIGINATE_SPACING_SEC:
                        remaining = ORIGINATE_SPACING_SEC - since_last_originate

                        print(
                            f"Celery: Originate pacing active. "
                            f"remaining={remaining:.1f}s. Waiting..."
                        )

                        break

                contact = pending_contacts.pop(0)

                existing_call = get_existing_call_log(
                    db,
                    campaign.id,
                    contact.id,
                    getattr(contact, "phone", ""),
                )

                if existing_call:
                    update_campaign_target_from_call(db, existing_call)
                    db.commit()
                    skipped_duplicates += 1
                    last_progress_at = time.monotonic()
                    continue

                trunk = get_available_sip_trunk(
                    db=db,
                    company_id=campaign.company_id,
                    selected_sip_trunk_id=selected_sip_trunk_id,
                )

                if not trunk:
                    pending_contacts.insert(0, contact)

                    print(
                        f"Celery: No SIP trunk slot available. "
                        f"active_slots={len(active_call_ids)}/{total_capacity}. Waiting..."
                    )

                    break

                try:
                    call_log = AsteriskService.initiate_call(
                        db=db,
                        campaign_id=campaign.id,
                        contact_id=contact.id,
                        phone_number=contact.phone,
                        trunk_id=trunk.id,
                        audio_filename=campaign.audio_file.filename,
                    )

                    active_call_ids.add(int(call_log.id))
                    triggered_calls += 1
                    last_originate_at = time.monotonic()
                    last_progress_at = time.monotonic()
                    contact_attempts.pop(contact.id, None)

                    update_campaign_target_from_call(db, call_log)
                    refresh_campaign_stats(db, campaign)
                    db.commit()

                    print(
                        f"Celery: Slot call submitted. "
                        f"phone={contact.phone}, "
                        f"call_log_id={call_log.id}, "
                        f"db_active={len(active_call_ids)}, "
                        f"cooldown={len(slot_cooldowns)}, "
                        f"capacity={total_capacity}, "
                        f"sip={trunk.number}/{trunk.asterisk_endpoint}"
                    )

                except Exception as e:
                    db.rollback()

                    attempts = int(contact_attempts.get(contact.id, 0)) + 1
                    contact_attempts[contact.id] = attempts

                    campaign = db.query(Campaign).filter(
                        Campaign.id == campaign_id,
                    ).first()

                    if not campaign or campaign.status == CampaignStatus.cancelled:
                        break

                    if attempts >= MAX_CONTACT_ATTEMPTS:
                        # Drop the contact instead of pushing it back forever.
                        abandoned_contacts += 1
                        last_progress_at = time.monotonic()

                        print(
                            f"Celery: Giving up on {contact.phone} after "
                            f"{attempts} failed originate attempts: {e}"
                        )
                    else:
                        pending_contacts.insert(0, contact)

                        print(
                            f"Celery error triggering slot call for {contact.phone} "
                            f"(attempt {attempts}/{MAX_CONTACT_ATTEMPTS}): {e}"
                        )

                    time.sleep(SLOT_WAIT_SEC)
                    break

            refresh_campaign_stats(db, campaign)
            db.commit()

            if pending_contacts or active_call_ids:
                time.sleep(SLOT_WAIT_SEC)

        db.refresh(campaign)

        if campaign.status != CampaignStatus.cancelled:
            wait_for_active_calls_to_finish(db, campaign, stuck_timeout_sec)

        db.refresh(campaign)

        if campaign.status == CampaignStatus.cancelled:
            cancelled_targets = mark_pending_targets_cancelled(db, campaign)
            db.commit()

            print(
                f"Celery: Campaign {campaign.id} remained cancelled. "
                f"cancelled_targets={cancelled_targets}"
            )

        elif pending_contacts:
            # We exited the scheduler with work left over, so this campaign did
            # not really complete. Do not report it as completed.
            refresh_campaign_stats(db, campaign)
            cancelled_targets = mark_pending_targets_cancelled(db, campaign)
            campaign.status = CampaignStatus.failed
            campaign.completed_at = utc_now()
            db.commit()

            print(
                f"Celery: Campaign {campaign.id} aborted with "
                f"{len(pending_contacts)} contact(s) never dialled. "
                f"cancelled_targets={cancelled_targets}"
            )

        else:
            refresh_campaign_stats(db, campaign)
            campaign.status = CampaignStatus.completed
            campaign.completed_at = utc_now()
            db.commit()

        print(
            f"Celery: Campaign {campaign_id} finished. "
            f"originated={triggered_calls}, skipped_duplicates={skipped_duplicates}, "
            f"abandoned={abandoned_contacts}."
        )

    except Exception as e:
        db.rollback()

        print(f"Celery: Critical error during campaign task: {e}")

        campaign = db.query(Campaign).filter(
            Campaign.id == campaign_id,
        ).first()

        if campaign and campaign.status != CampaignStatus.cancelled:
            campaign.status = CampaignStatus.failed
            campaign.completed_at = utc_now()
            db.commit()

    finally:
        db.close()