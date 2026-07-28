# app/services/sms_service.py

from __future__ import annotations

import base64
import os
import socket
import time
import uuid
from datetime import datetime, timezone
from typing import Optional

from app.config import settings
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

    if any(ord(ch) > 127 for ch in text):
        if len(text) <= 70:
            return 1
        return (len(text) + 66) // 67

    if len(text) <= 160:
        return 1

    return (len(text) + 152) // 153


def get_setting_value(*names, default=None):
    for name in names:
        value = getattr(settings, name, None)
        if value not in (None, ""):
            return value

    for name in names:
        value = os.getenv(name)
        if value not in (None, ""):
            return value

    return default


class AsteriskAMIClient:
    def __init__(self):
        self.host = settings.ASTERISK_AMI_HOST
        self.port = int(settings.ASTERISK_AMI_PORT)
        self.username = settings.ASTERISK_AMI_USER
        self.password = settings.ASTERISK_AMI_PASS
        self.timeout = float(
            get_setting_value(
                "AMI_TIMEOUT",
                "ASTERISK_AMI_TIMEOUT",
                default=10,
            )
        )

    def _read_response(self, sock: socket.socket) -> str:
        sock.settimeout(self.timeout)
        chunks = []

        while True:
            try:
                data = sock.recv(4096)

                if not data:
                    break

                chunks.append(data)

                joined = b"".join(chunks)

                if b"\r\n\r\n" in joined:
                    break

            except socket.timeout:
                # AMI banner sometimes does not end with a blank line.
                # Return whatever we already received instead of failing.
                break

        return b"".join(chunks).decode("utf-8", errors="replace")

    def _send_action(self, sock: socket.socket, fields: dict) -> str:
        payload = ""

        for key, value in fields.items():
            if value is None:
                continue

            value_text = str(value).replace("\r", " ").replace("\n", " ")
            payload += f"{key}: {value_text}\r\n"

        payload += "\r\n"

        sock.sendall(payload.encode("utf-8"))
        return self._read_response(sock)

    def send_message(
        self,
        endpoint: str,
        from_number: str,
        to_number: str,
        message_text: str,
    ) -> dict:
        action_id = f"sms-{uuid.uuid4().hex[:16]}"

        provider_host = "202.131.253.177"

        destination = f"pjsip:{endpoint}/sip:{to_number}@{provider_host}:5060"
        from_uri = f"sip:{from_number}@ip-phone.mobinet.mn"

        body_b64 = base64.b64encode(
            (message_text or "").encode("utf-8")
        ).decode("ascii")

        with socket.create_connection(
            (self.host, self.port),
            timeout=self.timeout,
        ) as sock:
            banner = self._read_response(sock)

            login_response = self._send_action(
                sock,
                {
                    "Action": "Login",
                    "Username": self.username,
                    "Secret": self.password,
                    "Events": "off",
                },
            )

            if "Response: Success" not in login_response:
                return {
                    "ok": False,
                    "error": "AMI login failed",
                    "provider_response": login_response,
                    "debug": banner,
                }

            response = self._send_action(
                sock,
                {
                    "Action": "MessageSend",
                    "ActionID": action_id,
                    "To": destination,
                    "From": from_uri,
                    "Base64Body": body_b64,
                },
            )

            self._send_action(sock, {"Action": "Logoff"})

        ok = "Response: Success" in response

        return {
            "ok": ok,
            "provider_message_id": action_id,
            "provider_response": response,
            "error": None if ok else "AMI MessageSend failed",
            "destination": destination,
            "to": destination,
            "from": from_uri,
        }


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
    def send_sip_message(
        phone: str,
        message_text: str,
        sip_trunk: SIPTrunk,
    ):
        endpoint = str(getattr(sip_trunk, "asterisk_endpoint", "") or "").strip()
        from_number = str(
            getattr(sip_trunk, "sms_sender_name", None)
            or getattr(sip_trunk, "number", "")
            or ""
        ).strip()

        if not endpoint:
            return {
                "ok": False,
                "error": "SIP endpoint is empty",
                "provider_response": None,
            }

        if not from_number:
            return {
                "ok": False,
                "error": "SMS sender number is empty",
                "provider_response": None,
            }

        client = AsteriskAMIClient()

        return client.send_message(
            endpoint=endpoint,
            from_number=from_number,
            to_number=phone,
            message_text=message_text,
        )

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

        sms_mode = str(getattr(sip_trunk, "sms_mode", "simulation") or "simulation").lower().strip()

        try:
            if sms_mode == "simulation":
                result = SMSService.send_simulation(
                    phone=phone,
                    message_text=sms_log.message_text,
                    sender_number=getattr(sip_trunk, "number", None),
                    sender_endpoint=getattr(sip_trunk, "asterisk_endpoint", None),
                )

            elif sms_mode == "sip_message":
                result = SMSService.send_sip_message(
                    phone=phone,
                    message_text=sms_log.message_text,
                    sip_trunk=sip_trunk,
                )

            elif sms_mode == "http_api":
                result = {
                    "ok": False,
                    "error": "HTTP API SMS mode is not implemented yet",
                    "provider_response": None,
                }

            elif sms_mode == "smpp":
                result = {
                    "ok": False,
                    "error": "SMPP SMS mode is not implemented yet",
                    "provider_response": None,
                }

            else:
                result = {
                    "ok": False,
                    "error": f"Unsupported SMS mode: {sms_mode}",
                    "provider_response": None,
                }

            if result.get("ok"):
                sms_log.status = "sent"
                sms_log.provider_message_id = result.get("provider_message_id")
                sms_log.provider_response = result.get("provider_response")
                sms_log.sent_at = utc_now()
                sms_log.error_message = None
            else:
                sms_log.status = "failed"
                sms_log.provider_message_id = result.get("provider_message_id")
                sms_log.provider_response = result.get("provider_response")
                sms_log.error_message = result.get("error") or "SMS send failed"

        except Exception as e:
            sms_log.status = "failed"
            sms_log.error_message = str(e)

        sms_log.updated_at = utc_now()
        db.commit()
        db.refresh(sms_log)

        return sms_log
