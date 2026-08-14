# run_listener.py

from __future__ import annotations

import re
import socket
import time
from datetime import datetime, timezone

from sqlalchemy import func, case

from app.config import settings
from app.database import SessionLocal
from app.models.call_log import CallLog, CallStatus
from app.models.campaign import Campaign, CampaignStatus
from app.models.campaign_target import CampaignTarget
from app.services.call_signal import signal_call_done


RTP_TIMEOUT_CAUSE = 44
NORMAL_CLEARING_CAUSE = 16
RTP_TIMEOUT_DELAY_SEC = 6.0

# How close raw channel time has to get to the audio file's length before we
# call it a full playback. Covers dialplan overhead between Playback()
# finishing and Hangup(), plus AMI event timestamping jitter.
FULL_PLAYBACK_TOLERANCE_SEC = 1.5

FINAL_STATUSES = {
    CallStatus.completed,
    CallStatus.failed,
    CallStatus.busy,
    CallStatus.no_answer,
    CallStatus.congestion,
}


def utc_now():
    return datetime.now(timezone.utc)


def safe_int(value, default: int = 0) -> int:
    try:
        return int(value or default)
    except (TypeError, ValueError):
        return default


def normalize_phone(phone) -> str:
    return "".join(
        ch for ch in str(phone or "").strip()
        if ch.isdigit()
    )


def status_value(status) -> str:
    if hasattr(status, "value"):
        return str(status.value)

    value = str(status or "").lower().strip()

    if "." in value:
        value = value.split(".")[-1]

    return value


def classify_status(cause_code, answered=False):
    cause_code = safe_int(cause_code)

    # If receiver picked up, status is completed.
    # Duration/RTP correction is handled separately by calculate_duration_sec().
    if answered:
        return CallStatus.completed

    # Cause 0 = "Unknown": Asterisk never got a real cause code back before
    # the channel tore down. Treat it as no_answer instead of falling through
    # to the generic `failed` default at the bottom - otherwise this silently
    # overwrites a more informative earlier guess (e.g. OriginateResponse's
    # Reason:3 -> no_answer) with a less informative one.
    if cause_code == 0:
        return CallStatus.no_answer

    # Normal clearing without answer evidence should not be completed.
    if cause_code == 16:
        return CallStatus.no_answer

    # Busy / rejected
    if cause_code in (17, 21, 22, 55, 57, 58):
        return CallStatus.busy

    # No answer / no user response
    if cause_code in (18, 19, 20, 27):
        return CallStatus.no_answer

    # RTP timeout without answer evidence.
    # If answered=True, the function already returned completed above.
    if cause_code == RTP_TIMEOUT_CAUSE:
        return CallStatus.no_answer

    # Provider/network congestion
    if cause_code in (34, 38, 41, 42, 50):
        return CallStatus.congestion

    # Bad number / incompatible destination
    if cause_code in (28, 88):
        return CallStatus.failed

    return CallStatus.failed

def classify_originate_failure(reason_code: int) -> CallStatus:
    if reason_code == 5:
        return CallStatus.busy

    if reason_code in [3, 8]:
        return CallStatus.no_answer

    return CallStatus.failed


def calculate_duration_sec(answered_at, ended_at, cause_code: int, audio_duration) -> float:
    """How many seconds of the message the person actually heard.

    Starts from time-on-call (answered -> ended) and adjusts per hangup cause.

    Full playback is detected by comparing raw channel time against the audio
    file's length. If the message ran to the end, the whole thing was heard,
    so report the file's length - raw time also includes dialplan overhead.

    An earlier version subtracted RTP_TIMEOUT_DELAY_SEC from cause 16 as well
    as cause 44, on the grounds that a full completion and an early hangup near
    the RTP-timeout boundary were indistinguishable. They are distinguishable:
    a full completion lands within FULL_PLAYBACK_TOLERANCE_SEC of
    audio_duration, an early hangup lands well short of it. That blanket
    subtraction under-reported every completed call by 6s and reported 0.00 for
    any message shorter than 6s - which is most TTS clips.

    For cause 44 (RTP timeout) the subtraction stays: the callee's audio
    genuinely stopped that many seconds before Asterisk declared the timeout,
    so that tail is measured detection lag, not listening. A cause 16 BYE is
    immediate and needs no such adjustment.
    """
    if not answered_at or not ended_at:
        return 0

    raw_duration = (ended_at - answered_at).total_seconds()

    # AMI events are timestamped when this listener processes them, so a late
    # event can place answered_at after ended_at. Treat that as unmeasurable.
    if raw_duration <= 0:
        return 0

    audio = float(audio_duration or 0)

    # Ran to the end of the file: everything was heard.
    if audio > 0 and raw_duration >= audio - FULL_PLAYBACK_TOLERANCE_SEC:
        return round(audio, 2)

    listened = raw_duration

    if cause_code == RTP_TIMEOUT_CAUSE:
        listened -= RTP_TIMEOUT_DELAY_SEC

    if listened <= 0:
        return 0

    # Cannot have heard more of the message than the message contains.
    if audio > 0:
        listened = min(listened, audio)

    return round(listened, 2)

def extract_call_log_id_from_event(event: dict):
    direct_keys = [
        "CALL_LOG_ID",
        "CallLogID",
        "call_log_id",
    ]

    for key in direct_keys:
        value = event.get(key)
        if value:
            call_id = safe_int(value, 0)
            if call_id > 0:
                return call_id

    variable = event.get("Variable", "")
    if "CALL_LOG_ID=" in variable:
        match = re.search(r"CALL_LOG_ID=([0-9]+)", variable)
        if match:
            return safe_int(match.group(1), 0)

    value = event.get("Value", "")
    variable_name = event.get("Variable", "")

    if variable_name == "CALL_LOG_ID":
        call_id = safe_int(value, 0)
        if call_id > 0:
            return call_id

    channel_fields = [
        event.get("Channel", ""),
        event.get("DestChannel", ""),
        event.get("Uniqueid", ""),
        event.get("DestUniqueid", ""),
        event.get("Linkedid", ""),
    ]

    for field in channel_fields:
        match = re.search(r"vc-([0-9]+)-", str(field or ""))
        if match:
            call_id = safe_int(match.group(1), 0)
            if call_id > 0:
                return call_id

    return None


def update_campaign_target_from_call(db, call):
    if not call:
        return

    phone = normalize_phone(call.phone)

    target = None

    if call.id:
        target = db.query(CampaignTarget).filter(
            CampaignTarget.call_log_id == call.id,
        ).first()

    if not target and phone:
        target = db.query(CampaignTarget).filter(
            CampaignTarget.campaign_id == call.campaign_id,
            CampaignTarget.phone == phone,
        ).first()

    if not target:
        return

    if target.status == "cancelled" and not target.call_log_id:
        return

    target.call_log_id = call.id
    target.status = status_value(call.status)
    target.updated_at = utc_now()


def refresh_campaign_stats(db, campaign_id: int):
    campaign = db.query(Campaign).filter(
        Campaign.id == campaign_id,
    ).first()

    if not campaign:
        return

    stats = db.query(
        func.count(CallLog.id).label("total_calls"),
        func.sum(case((CallLog.status == CallStatus.completed, 1), else_=0)).label("completed_calls"),
        func.sum(case((CallLog.status == CallStatus.busy, 1), else_=0)).label("busy_calls"),
        func.sum(case((CallLog.status == CallStatus.no_answer, 1), else_=0)).label("no_answer_calls"),
        func.sum(case((CallLog.status == CallStatus.failed, 1), else_=0)).label("failed_only_calls"),
        func.sum(case((CallLog.status == CallStatus.congestion, 1), else_=0)).label("congestion_calls"),
    ).filter(
        CallLog.campaign_id == campaign_id,
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


def parse_ami_block(block: str) -> dict:
    event = {}

    for line in block.strip().splitlines():
        if ":" in line:
            key, val = line.split(":", 1)
            event[key.strip()] = val.strip()

    return event


def find_call_by_event(db, event: dict):
    call_log_id = extract_call_log_id_from_event(event)

    if call_log_id:
        call = db.query(CallLog).filter(
            CallLog.id == call_log_id,
        ).first()

        if call:
            return call

    action_id = event.get("ActionID", "")

    if action_id.startswith("act_"):
        call = db.query(CallLog).filter(
            CallLog.ami_action_id == action_id,
        ).first()

        if call:
            return call

    unique_ids = [
        event.get("Uniqueid", ""),
        event.get("DestUniqueid", ""),
        event.get("Linkedid", ""),
    ]

    unique_ids = [
        uid for uid in unique_ids
        if uid and uid != "<unknown>"
    ]

    for unique_id in unique_ids:
        call = db.query(CallLog).filter(
            CallLog.ami_unique_id == unique_id,
        ).first()

        if call:
            return call

    return None


def get_audio_duration(call: CallLog):
    try:
        if call.campaign and call.campaign.audio_file:
            return call.campaign.audio_file.duration_sec
    except Exception:
        return None

    return None


def handle_originate_response(db, event: dict):
    call = find_call_by_event(db, event)

    if not call:
        return

    response = event.get("Response")
    unique_id = event.get("Uniqueid", "")

    if response == "Success" and unique_id and unique_id != "<unknown>":
        call.ami_unique_id = unique_id

        if not call.answered_at:
            call.answered_at = utc_now()

        db.commit()

        print(f"AMI ANSWER: Call {call.phone} answered. uniqueid={unique_id}")
        return

    if response == "Failure" and not call.ended_at:
        call.ended_at = utc_now()

        reason = safe_int(event.get("Reason"))

        call.hangup_cause = reason
        call.status = classify_originate_failure(reason)
        call.duration_sec = 0

        update_campaign_target_from_call(db, call)
        refresh_campaign_stats(db, call.campaign_id)

        db.commit()

        signal_call_done(call.id, status_value(call.status))

        print(f"AMI FAILED: Call {call.phone} failed before answer. reason={reason}")


def handle_dial_end(db, event: dict):
    call = find_call_by_event(db, event)

    if not call:
        return

    dial_status = str(event.get("DialStatus", "")).upper().strip()

    if dial_status == "ANSWER":
        if not call.answered_at:
            call.answered_at = utc_now()
            db.commit()

        print(f"AMI DIALEND ANSWER: {call.phone}")
        return

    # Do not set failed/congestion/no_answer here.
    # Hangup cause is final truth.
    print(
        f"AMI DIALEND PRELIMINARY: phone={call.phone}, "
        f"dial_status={dial_status}. Waiting for Hangup."
    )


def handle_bridge_enter(db, event: dict):
    call = find_call_by_event(db, event)

    if call and not call.answered_at:
        call.answered_at = utc_now()
        db.commit()
        print(f"AMI BRIDGE ANSWER: {call.phone}")


def handle_newstate(db, event: dict):
    if event.get("ChannelStateDesc") != "Up":
        return

    call = find_call_by_event(db, event)

    if not call:
        return

    if not call.answered_at:
        call.answered_at = utc_now()

    # If Celery already marked it congestion/stuck too early,
    # clear ended_at so final Hangup can complete it correctly.
    if call.ended_at and status_value(call.status) != "completed":
        print(
            f"AMI ANSWER RESCUE: {call.phone} answered after early final status. "
            f"old_status={call.status}"
        )
        call.ended_at = None
        call.duration_sec = 0
        call.hangup_cause = None
        call.status = CallStatus.calling

    db.commit()

    print(f"AMI UP ANSWER: {call.phone}")


def handle_hangup(db, event: dict):
    call = find_call_by_event(db, event)

    if not call:
        return

    cause_code = safe_int(event.get("Cause"))
    channel_state = str(event.get("ChannelStateDesc", "") or "").lower().strip()

    answered = call.answered_at is not None or channel_state == "up"

    now = utc_now()

    if answered and not call.answered_at:
        call.answered_at = call.started_at or now

    # IMPORTANT:
    # Hangup is final truth. Even if DialEnd/OriginateResponse/Celery already
    # marked this call as failed/congestion/no_answer, reclassify by final cause.
    call.ended_at = now
    call.hangup_cause = cause_code

    call.status = classify_status(cause_code, answered)

    if call.status == CallStatus.completed:
        audio_duration = get_audio_duration(call)

        call.duration_sec = calculate_duration_sec(
            answered_at=call.answered_at,
            ended_at=call.ended_at,
            cause_code=cause_code,
            audio_duration=audio_duration,
        )

        print(
            f"AMI HANGUP FINAL: {call.phone} completed. "
            f"duration={call.duration_sec:.2f}s "
            f"cause={cause_code} "
            f"answered={answered}"
        )

    else:
        call.duration_sec = 0

        print(
            f"AMI HANGUP FINAL: {call.phone} status={call.status} "
            f"cause={cause_code} "
            f"answered={answered}"
        )

    update_campaign_target_from_call(db, call)
    refresh_campaign_stats(db, call.campaign_id)

    db.commit()

    signal_call_done(call.id, status_value(call.status))



def handle_ami_event(event: dict):
    event_type = event.get("Event")

    if not event_type:
        return

    if event_type in ["OriginateResponse", "Hangup", "DialEnd", "BridgeEnter", "Newstate"]:
        print("AMI DEBUG:", event)

    db = SessionLocal()

    try:
        if event_type == "OriginateResponse":
            handle_originate_response(db, event)

        elif event_type == "DialEnd":
            handle_dial_end(db, event)

        elif event_type == "BridgeEnter":
            handle_bridge_enter(db, event)

        elif event_type == "Newstate":
            handle_newstate(db, event)

        elif event_type == "Hangup":
            handle_hangup(db, event)

    except Exception as e:
        db.rollback()
        print(f"Error handling event: {e}")

    finally:
        db.close()


def read_ami_block(sock: socket.socket, timeout: float = 10.0) -> str:
    sock.settimeout(timeout)

    buffer = ""

    while "\r\n\r\n" not in buffer and "\n\n" not in buffer:
        chunk = sock.recv(4096)

        if not chunk:
            break

        buffer += chunk.decode("utf-8", errors="ignore")

    return buffer


def start_ami_listener():
    print(
        f"Starting Asterisk AMI Listener on "
        f"{settings.ASTERISK_AMI_HOST}:{settings.ASTERISK_AMI_PORT}..."
    )

    while True:
        sock = None

        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(10.0)
            sock.connect((settings.ASTERISK_AMI_HOST, settings.ASTERISK_AMI_PORT))

            try:
                banner = read_ami_block(sock, timeout=5.0)
                print("AMI BANNER:", banner.strip())
            except socket.timeout:
                print("AMI BANNER: timeout, continuing login")

            login_cmd = (
                "Action: Login\r\n"
                f"Username: {settings.ASTERISK_AMI_USER}\r\n"
                f"Secret: {settings.ASTERISK_AMI_PASS}\r\n"
                "Events: on\r\n\r\n"
            )

            sock.sendall(login_cmd.encode("utf-8"))

            response = read_ami_block(sock, timeout=10.0)

            if "Response: Success" not in response:
                print("AMI Login Failed. Retrying in 5 seconds...")
                print(response.strip())

                try:
                    sock.close()
                except Exception:
                    pass

                time.sleep(5)
                continue

            print("AMI Login Successful. Listening to events...")

            sock.settimeout(None)

            buffer = ""

            while True:
                data = sock.recv(4096)

                if not data:
                    print("AMI connection closed by Asterisk.")
                    break

                buffer += data.decode("utf-8", errors="ignore")

                while "\r\n\r\n" in buffer:
                    block, buffer = buffer.split("\r\n\r\n", 1)

                    event = parse_ami_block(block)

                    if event:
                        handle_ami_event(event)

        except Exception as e:
            print(f"Socket connection lost: {e}. Reconnecting in 5 seconds...")

        finally:
            if sock:
                try:
                    sock.close()
                except Exception:
                    pass

            time.sleep(5)


if __name__ == "__main__":
    start_ami_listener()