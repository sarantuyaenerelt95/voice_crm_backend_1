# app/services/asterisk.py
import socket
import uuid
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.config import settings
from app.models.sip_trunk import SIPTrunk
from app.models.call_log import CallLog, CallStatus
from sqlalchemy.exc import IntegrityError
from app.models.campaign_target import CampaignTarget


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

        Important:
        We use Async: true. After sending Originate, return True immediately.
        run_listener.py will decide final status from OriginateResponse/Hangup events.
        """
        sock = None

        try:
            clean_audio_filename = AsteriskService._clean_audio_filename(audio_filename)
            endpoint = str(trunk.asterisk_endpoint or "").strip()

            if not endpoint:
                print("Asterisk originate failed: trunk endpoint is empty")
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

            # Read AMI banner
            # AMI banner is often only one line, not a full \r\n\r\n block.
            try:
                sock.settimeout(2.0)
                banner = sock.recv(4096).decode("utf-8", errors="ignore")
                print("AMI BANNER:", banner.strip())
            except socket.timeout:
                print("AMI BANNER: no banner received, continuing to login")

            sock.settimeout(10.0)

            login_cmd = (
                f"Action: Login\r\n"
                f"Username: {settings.ASTERISK_AMI_USER}\r\n"
                f"Secret: {settings.ASTERISK_AMI_PASS}\r\n"
                f"Events: off\r\n\r\n"
            )

            sock.sendall(login_cmd.encode("utf-8"))
            login_response = AsteriskService._read_ami_block(sock, timeout=10.0)

            print("AMI LOGIN RESPONSE:", login_response.strip())

            if "Response: Success" not in login_response:
                print(f"Asterisk AMI login failed: {login_response.strip()}")
                return False

            originate_cmd = (
                f"Action: Originate\r\n"
                f"Channel: PJSIP/{phone_number}@{endpoint}\r\n"
                f"ChannelId: {channel_id}\r\n"
                f"Context: broadcast\r\n"
                f"Exten: s\r\n"
                f"Priority: 1\r\n"
                f"CallerID: {trunk.number} <Voice CRM>\r\n"
                f"Timeout: 12000\r\n"
                f"Variable: AUDIO_FILE={clean_audio_filename}\r\n"
                f"Variable: TARGET_USER={phone_number}\r\n"
                f"Variable: CALL_LOG_ID={call_log_id}\r\n"
                f"ActionID: {action_id}\r\n"
                f"Async: true\r\n\r\n"
            )

            print(
                f"Asterisk originate sending: "
                f"phone={phone_number}, endpoint={endpoint}, "
                f"action_id={action_id}, channel_id={channel_id}"
            )

            sock.sendall(originate_cmd.encode("utf-8"))

            # Do NOT wait for originate response here.
            # The AMI listener receives OriginateResponse/Hangup events.
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

        normalized_phone = "".join(
            ch for ch in str(phone_number or "").strip()
            if ch.isdigit()
        )

        if not normalized_phone:
            raise ValueError("Phone number is empty after normalization.")

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

        phone_number = normalized_phone

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
            phone_number=phone_number,
            trunk=trunk,
            audio_filename=audio_filename,
            action_id=action_id,
            call_log_id=call_log.id,
            channel_id=channel_id,
        )

        if not success:
            call_log.status = CallStatus.failed
            call_log.ended_at = datetime.now(timezone.utc)
            call_log.duration_sec = 0
            db.commit()
            db.refresh(call_log)

        return call_log