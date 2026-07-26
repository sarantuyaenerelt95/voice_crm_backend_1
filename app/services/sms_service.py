# app/services/sms_service.py

from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Optional

from app.models.sms_blacklist import SMSBlacklist
from app.models.sms_log import SMSLog
from app.models.sip_trunk import SIPTrunk


def utc_now():
    return datetime.now(timezone.utc)


def normalize_phone(phone: Optional[str]) -> str:
    return "".join(
        ch for ch in str(phone or "").strip()
        if ch.isdigit()
    )


def is_valid_sms_phone(phone: str) -> bool:
    phone = normalize_phone(phone)

    if len(phone) == 8:
        return True

    if phone.startswith("976") and len(phone) == 11:
        return True

    return False


def estimate_sms_segments(message_text: str) -> int:
    text = message_text or ""

    if not text:
        return 0

    # Mongolian Cyrillic uses Unicode SMS.
    if any(ord(ch) > 127 for ch in text):
        if len(text) <= 70:
            return 1
        return (len(text) + 66) // 67

    if len(text) <= 160:
        return 1

    return (len(text) + 152) // 153


class SMSService:
    @staticmethod
    def send_simulation(
        phone: str,
        message_text: str,
        sender_number: Optional[str] = None,
        sender_endpoint: Optional[str] = None,
    ):
        time.sleep(0.05)

        return {
            "ok": True,
            "provider_message_id": f"sim-sip-{sender_number or 'unknown'}-{int(time.time() * 1000)}",
            "provider_response": (
                f"SIMULATION_SENT via SIP number={sender_number}, "
                f"endpoint={sender_endpoint}"
            ),
        }

    @staticmethod
    def send_log(
        db,
        sms_log: SMSLog,
        sip_trunk: SIPTrunk,
        company_id: int,
    ) -> SMSLog:
        phone = normalize_phone(sms_log.phone)

        sms_log.phone = phone
        sms_log.status = "sending"
        sms_log.updated_at = utc_now()
        db.commit()
        db.refresh(sms_log)

        if not is_valid_sms_phone(phone):
            sms_log.status = "failed"
            sms_log.error_message = "Invalid phone number"
            sms_log.updated_at = utc_now()
            db.commit()
            db.refresh(sms_log)
            return sms_log

        if not sip_trunk:
            sms_log.status = "failed"
            sms_log.error_message = "No SIP number selected for SMS"
            sms_log.updated_at = utc_now()
            db.commit()
            db.refresh(sms_log)
            return sms_log

        blacklisted = db.query(SMSBlacklist).filter(
            SMSBlacklist.company_id == company_id,
            SMSBlacklist.phone == phone,
        ).first()

        if blacklisted:
            sms_log.status = "failed"
            sms_log.error_message = "Phone is blacklisted"
            sms_log.updated_at = utc_now()
            db.commit()
            db.refresh(sms_log)
            return sms_log

        try:
            result = SMSService.send_simulation(
                phone=phone,
                message_text=sms_log.message_text,
                sender_number=getattr(sip_trunk, "number", None),
                sender_endpoint=getattr(sip_trunk, "asterisk_endpoint", None),
            )

            if result.get("ok"):
                sms_log.status = "sent"
                sms_log.provider_message_id = result.get("provider_message_id")
                sms_log.provider_response = result.get("provider_response")
                sms_log.sent_at = utc_now()
                sms_log.error_message = None
            else:
                sms_log.status = "failed"
                sms_log.provider_response = result.get("provider_response")
                sms_log.error_message = result.get("error") or "SMS send failed"

        except Exception as e:
            sms_log.status = "failed"
            sms_log.error_message = str(e)

        sms_log.updated_at = utc_now()
        db.commit()
        db.refresh(sms_log)

        return sms_log
