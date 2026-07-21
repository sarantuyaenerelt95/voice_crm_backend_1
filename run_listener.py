import socket
import time
from datetime import datetime, timezone

from app.config import settings
from app.database import SessionLocal
from app.models.call_log import CallLog, CallStatus
from app.models.campaign import Campaign, CampaignStatus
from app.services.call_signal import signal_call_done
from sqlalchemy import func, case
from app.models.campaign_target import CampaignTarget

RTP_TIMEOUT_CAUSE = 44
RTP_TIMEOUT_DELAY_SEC = 6.0
FINAL_STATUSES = {
    CallStatus.completed,
    CallStatus.failed,
    CallStatus.busy,
    CallStatus.no_answer,
    CallStatus.congestion,
}


def safe_int(value, default: int = 0) -> int:
    try:
        return int(value or default)
    except (TypeError, ValueError):
        return default


def classify_status(cause_code: int, answered: bool) -> CallStatus:
    # Completed / answered cases
    if cause_code == 16:
        return CallStatus.completed if answered else CallStatus.failed

    # Unknown cause
    if cause_code == 0:
        return CallStatus.completed if answered else CallStatus.failed

    # RTP timeout after answer.
    # Mobinet answered + cause 44 means audio played, then RTP timeout closed the call.
    if cause_code == 44:
        return CallStatus.completed if answered else CallStatus.congestion

    # Busy / rejected / barred / bearer unavailable
    if cause_code in [17, 21, 22, 55, 57, 58]:
        return CallStatus.busy

    # No answer / absent / destination out of order
    if cause_code in [18, 19, 20, 27]:
        return CallStatus.no_answer

    # Network / congestion
    if cause_code in [34, 38, 41, 42, 50]:
        return CallStatus.congestion

    # Invalid number / incompatible destination
    if cause_code in [28, 88]:
        return CallStatus.failed

    if answered:
        return CallStatus.completed

    return CallStatus.failed


def classify_originate_failure(reason_code: int) -> CallStatus:
    if reason_code == 5:
        return CallStatus.busy

    if reason_code in [3, 8]:
        return CallStatus.no_answer

    return CallStatus.failed


def calculate_duration_sec(answered_at, ended_at, cause_code: int, audio_duration) -> float:
    if not answered_at or not ended_at:
        return 0

    raw_duration = max(0, (ended_at - answered_at).total_seconds())

    if cause_code == RTP_TIMEOUT_CAUSE:
        estimated_duration = max(0, raw_duration - RTP_TIMEOUT_DELAY_SEC)
    else:
        estimated_duration = raw_duration

    if audio_duration is not None and audio_duration > 0:
        estimated_duration = min(estimated_duration, float(audio_duration))

    return round(estimated_duration, 2)


def update_campaign_target_from_call(db, call):
    if not call:
        return

    phone = "".join(ch for ch in str(call.phone or "").strip() if ch.isdigit())

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
    target.status = call.status.value if hasattr(call.status, "value") else str(call.status)

def refresh_campaign_stats(db, campaign_id: int):
    campaign = db.query(Campaign).filter(Campaign.id == campaign_id).first()
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
        CallLog.campaign_id == campaign_id
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
            campaign.completed_at = datetime.now(timezone.utc)


def parse_ami_block(block: str) -> dict:
    event = {}
    for line in block.strip().splitlines():
        if ":" in line:
            key, val = line.split(":", 1)
            event[key.strip()] = val.strip()
    return event


def find_call_by_event(db, event: dict):
    action_id = event.get("ActionID", "")
    unique_ids = [
        event.get("Uniqueid", ""),
        event.get("DestUniqueid", ""),
        event.get("Linkedid", ""),
    ]
    unique_ids = [uid for uid in unique_ids if uid and uid != "<unknown>"]

    if action_id.startswith("act_"):
        call = db.query(CallLog).filter(CallLog.ami_action_id == action_id).first()
        if call:
            return call

    for unique_id in unique_ids:
        call = db.query(CallLog).filter(CallLog.ami_unique_id == unique_id).first()
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
            call.answered_at = datetime.now(timezone.utc)
        db.commit()
        print(f"AMI ANSWER: Call {call.phone} answered. uniqueid={unique_id}")
        return

    if response == "Failure" and not call.ended_at:
        call.ended_at = datetime.now(timezone.utc)
        reason = safe_int(event.get("Reason"))
        call.hangup_cause = reason
        call.status = classify_originate_failure(reason)
        call.duration_sec = 0
        update_campaign_target_from_call(db, call)
        refresh_campaign_stats(db, call.campaign_id)
        db.commit()
        signal_call_done(call.id, call.status.value)
        print(f"AMI FAILED: Call {call.phone} failed before answer. reason={reason}")

def handle_dial_end(db, event: dict):
    call = find_call_by_event(db, event)

    if not call:
        return

    dial_status = str(event.get("DialStatus", "")).upper().strip()

    if dial_status == "ANSWER":
        if not call.answered_at:
            call.answered_at = datetime.now(timezone.utc)
            db.commit()
        print(f"AMI DIALEND ANSWER: {call.phone}")
        return

    # If already finished, do not overwrite final status.
    if call.ended_at:
        return

    if dial_status == "BUSY":
        call.status = CallStatus.busy
    elif dial_status == "NOANSWER":
        call.status = CallStatus.no_answer
    elif dial_status in ["CONGESTION", "CHANUNAVAIL"]:
        call.status = CallStatus.congestion
    elif dial_status in ["CANCEL", "DONTCALL", "TORTURE", "INVALIDARGS"]:
        call.status = CallStatus.failed
    else:
        return

    call.ended_at = datetime.now(timezone.utc)
    call.duration_sec = 0
    update_campaign_target_from_call(db, call)
    refresh_campaign_stats(db, call.campaign_id)
    db.commit()

    signal_call_done(call.id, call.status.value)

    print(
        f"AMI DIALEND: {call.phone} "
        f"status={call.status} dial_status={dial_status}"
    )

def handle_hangup(db, event: dict):
    call = find_call_by_event(db, event)
    if not call or call.ended_at:
        return

    call.ended_at = datetime.now(timezone.utc)
    cause_code = safe_int(event.get("Cause"))
    call.hangup_cause = cause_code

    answered = call.answered_at is not None
    call.status = classify_status(cause_code, answered)

    if call.status == CallStatus.completed:
        audio_duration = get_audio_duration(call)
        call.duration_sec = calculate_duration_sec(
            answered_at=call.answered_at,
            ended_at=call.ended_at,
            cause_code=cause_code,
            audio_duration=audio_duration,
        )
        raw_duration = (call.ended_at - call.answered_at).total_seconds() if call.answered_at else 0
        print(
            f"AMI HANGUP: {call.phone} completed. "
            f"raw={raw_duration:.2f}s audio={audio_duration} "
            f"saved={call.duration_sec:.2f}s cause={cause_code}"
        )
    else:
        call.duration_sec = 0
        print(
            f"AMI HANGUP: {call.phone} status={call.status} "
            f"cause={cause_code} answered={answered}"
        )

    update_campaign_target_from_call(db, call)
    refresh_campaign_stats(db, call.campaign_id)
    db.commit()
    signal_call_done(call.id, call.status.value)


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
            call = find_call_by_event(db, event)
            if call and not call.answered_at:
                call.answered_at = datetime.now(timezone.utc)
                db.commit()
                print(f"AMI BRIDGE ANSWER: {call.phone}")

        elif event_type == "Newstate" and event.get("ChannelStateDesc") == "Up":
            call = find_call_by_event(db, event)
            if call and not call.answered_at:
                call.answered_at = datetime.now(timezone.utc)
                db.commit()
                print(f"AMI UP ANSWER: {call.phone}")

        elif event_type == "Hangup":
            handle_hangup(db, event)
    except Exception as e:
        db.rollback()
        print(f"Error handling event: {e}")
    finally:
        db.close()


def start_ami_listener():
    print(
        f"Starting Asterisk AMI Listener on "
        f"{settings.ASTERISK_AMI_HOST}:{settings.ASTERISK_AMI_PORT}..."
    )

    while True:
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.connect((settings.ASTERISK_AMI_HOST, settings.ASTERISK_AMI_PORT))
            sock.recv(1024)

            login_cmd = (
                f"Action: Login\r\n"
                f"Username: {settings.ASTERISK_AMI_USER}\r\n"
                f"Secret: {settings.ASTERISK_AMI_PASS}\r\n"
                f"Events: on\r\n\r\n"
            )
            sock.sendall(login_cmd.encode("utf-8"))
            response = sock.recv(1024).decode("utf-8", errors="ignore")

            if "Success" not in response:
                print("AMI Login Failed. Retrying in 5 seconds...")
                sock.close()
                time.sleep(5)
                continue

            print("AMI Login Successful. Listening to events...")

            buffer = ""
            while True:
                data = sock.recv(4096).decode("utf-8", errors="ignore")
                if not data:
                    print("AMI connection closed by Asterisk.")
                    break

                buffer += data
                while "\r\n\r\n" in buffer:
                    block, buffer = buffer.split("\r\n\r\n", 1)
                    handle_ami_event(parse_ami_block(block))

        except Exception as e:
            print(f"Socket connection lost: {e}. Reconnecting in 5 seconds...")
            time.sleep(5)


if __name__ == "__main__":
    start_ami_listener()
