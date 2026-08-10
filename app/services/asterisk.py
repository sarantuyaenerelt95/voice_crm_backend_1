# app/services/asterisk.py

from __future__ import annotations

import socket
import uuid
from datetime import datetime, timezone

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import settings
from app.models.sip_trunk import SIPTrunk
from app.models.call_log import CallLog, CallStatus
from app.models.campaign_target import CampaignTarget


# Maximum time Asterisk keeps ringing one originate before giving up.
# Observed pickups on this system land between ~6s and ~18s, so 30s covers
# real answers with margin while freeing the slot quickly on no-answer.
# The Celery stuck-call sweeper must wait LONGER than this, otherwise it
# marks a call as congestion while Asterisk is still legitimately ringing.
# See MIN_STUCK_CALL_TIMEOUT_SEC in app/tasks/campaign_tasks.py.
ORIGINATE_TIMEOUT_SEC = 20.0


class AsteriskService:
    @staticmethod
    def _read_ami_block(sock: socket.socket, timeout: float = 5.0) -> str:
        sock.settimeout(timeout)
        buffer = ""

        while "\r\n\r\n" not in buffer and "\n\n" not in buffer:
            chunk = sock.recv(4096)

            if not chunk:
                break

            buffer += chunk.decode("utf-8", errors="ignore")

        return buffer

    @staticmethod
    def _clean_audio_filename(audio_filename: str) -> str:
        audio_filename = str(audio_filename or "").strip()
        return audio_filename.rsplit(".", 1)[0] if "." in audio_filename else audio_filename

    @staticmethod
    def send_ami_originate(
        phone_number: str,
        trunk: SIPTrunk,
        audio_filename: str,
        action_id: str,
        call_log_id: int,
        channel_id: str,
    ) -> bool:
        """
        Connect to Asterisk AMI and submit one Originate action.

        We use Async: true.
        run_listener.py decides final status from OriginateResponse / DialEnd / Hangup.
        """
        sock = None

        try:
            clean_audio_filename = AsteriskService._clean_audio_filename(audio_filename)
            endpoint = str(trunk.asterisk_endpoint or "").strip()

            if not endpoint:
                print("Asterisk originate failed: trunk endpoint is empty")
                return False

            if not clean_audio_filename:
                print("Asterisk originate failed: audio filename is empty")
                return False

            print(
                f"AMI DEBUG settings: host={settings.ASTERISK_AMI_HOST}, "
                f"port={settings.ASTERISK_AMI_PORT}, "
                f"user={settings.ASTERISK_AMI_USER}, "
                f"pass_len={len(settings.ASTERISK_AMI_PASS or '')}"
            )

            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(10.0)
            sock.connect((settings.ASTERISK_AMI_HOST, settings.ASTERISK_AMI_PORT))

            try:
                sock.settimeout(2.0)
                banner = sock.recv(4096).decode("utf-8", errors="ignore")
                print("AMI BANNER:", banner.strip())
            except socket.timeout:
                print("AMI BANNER: no banner received, continuing to login")

            sock.settimeout(10.0)

            login_cmd = (
                "Action: Login\r\n"
                f"Username: {settings.ASTERISK_AMI_USER}\r\n"
                f"Secret: {settings.ASTERISK_AMI_PASS}\r\n"
                "Events: off\r\n\r\n"
            )

            sock.sendall(login_cmd.encode("utf-8"))
            login_response = AsteriskService._read_ami_block(sock, timeout=10.0)

            print("AMI LOGIN RESPONSE:", login_response.strip())

            if "Response: Success" not in login_response:
                print(f"Asterisk AMI login failed: {login_response.strip()}")
                return False

            clean_audio_filename = AsteriskService._clean_audio_filename(audio_filename)

            message = (
                "Action: Originate\r\n"
                f"Channel: PJSIP/{phone_number}@{endpoint}\r\n"
                f"ChannelId: {channel_id}\r\n"
                "Context: broadcast\r\n"
                "Exten: s\r\n"
                "Priority: 1\r\n"
                f"CallerID: \"Voice CRM\" <{trunk.number}>\r\n"
                f"Timeout: {int(ORIGINATE_TIMEOUT_SEC * 1000)}\r\n"
                f"Variable: AUDIO_FILE={clean_audio_filename}\r\n"
                f"Variable: TARGET_USER={phone_number}\r\n"
                f"Variable: CALL_LOG_ID={call_log_id}\r\n"
                f"ActionID: {action_id}\r\n"
                "Async: true\r\n"
                "\r\n"
            )

            print(
                f"Asterisk originate sending: "
                f"phone={phone_number}, endpoint={endpoint}, "
                f"action_id={action_id}, channel_id={channel_id}"
            )

            sock.sendall(message.encode("utf-8"))

            print(
                f"Asterisk originate submitted to AMI. "
                f"phone={phone_number}, endpoint={endpoint}"
            )

            return True

        except Exception as e:
            print(f"Asterisk AMI connection failed before/while sending originate: {e}")
            return False

        finally:
            if sock:
                try:
                    sock.sendall(b"Action: Logoff\r\n\r\n")
                except Exception:
                    pass

                try:
                    sock.close()
                except Exception:
                    pass

    @classmethod
    def initiate_call(
        cls,
        db: Session,
        campaign_id: int,
        contact_id: int,
        phone_number: str,
        trunk_id: int,
        audio_filename: str,
    ) -> CallLog:
        """Create a CallLog and submit one physical call to Asterisk."""
        trunk = db.query(SIPTrunk).filter(SIPTrunk.id == trunk_id).first()

        if not trunk:
            raise ValueError("SIP Trunk not found")

        if hasattr(trunk, "is_active") and trunk.is_active is False:
            raise ValueError("SIP Trunk is inactive")

        normalized_phone = "".join(
            ch for ch in str(phone_number or "").strip()
            if ch.isdigit()
        )

        if not normalized_phone:
            raise ValueError("Phone number is empty after normalization.")

        clean_audio_filename = cls._clean_audio_filename(audio_filename)

        if not clean_audio_filename:
            raise ValueError("Audio filename is empty.")

        existing_call = db.query(CallLog).filter(
            CallLog.campaign_id == campaign_id,
            CallLog.phone == normalized_phone,
        ).first()

        if existing_call:
            print(
                f"AsteriskService: Duplicate phone call prevented. "
                f"campaign_id={campaign_id}, phone={normalized_phone}, "
                f"existing_call_id={existing_call.id}"
            )
            return existing_call

        action_id = f"act_{uuid.uuid4().hex[:12]}"

        call_log = CallLog(
            campaign_id=campaign_id,
            contact_id=contact_id,
            trunk_id=trunk.id,
            phone=normalized_phone,
            status=CallStatus.calling,
            ami_action_id=action_id,
        )

        try:
            db.add(call_log)
            db.commit()
            db.refresh(call_log)

        except IntegrityError:
            db.rollback()

            existing_call = db.query(CallLog).filter(
                CallLog.campaign_id == campaign_id,
                CallLog.phone == normalized_phone,
            ).first()

            if existing_call:
                print(
                    f"AsteriskService: Duplicate insert race prevented. "
                    f"campaign_id={campaign_id}, phone={normalized_phone}, "
                    f"existing_call_id={existing_call.id}"
                )
                return existing_call

            raise

        target = db.query(CampaignTarget).filter(
            CampaignTarget.campaign_id == campaign_id,
            CampaignTarget.phone == normalized_phone,
        ).first()

        if target:
            target.status = "calling"
            target.call_log_id = call_log.id
            target.attempts = int(target.attempts or 0) + 1
            db.commit()

        channel_id = f"vc-{call_log.id}-{uuid.uuid4().hex[:8]}"

        call_log.ami_unique_id = channel_id
        db.commit()
        db.refresh(call_log)

        success = cls.send_ami_originate(
            phone_number=normalized_phone,
            trunk=trunk,
            audio_filename=clean_audio_filename,
            action_id=action_id,
            call_log_id=call_log.id,
            channel_id=channel_id,
        )

        if not success:
            now = datetime.now(timezone.utc)

            call_log.status = CallStatus.failed
            call_log.ended_at = now
            call_log.duration_sec = 0

            if target:
                target.status = "failed"

            db.commit()
            db.refresh(call_log)

        return call_log