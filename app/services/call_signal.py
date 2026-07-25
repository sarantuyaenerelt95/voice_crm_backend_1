# app/services/call_signal.py

from __future__ import annotations

import json
import time
from typing import Optional, Dict, Any

import redis

from app.config import settings


CHANNEL_PREFIX = "voicecrm:call_done:"


def _redis_client():
    url = getattr(settings, "REDIS_URL", "redis://localhost:6379/0")
    return redis.Redis.from_url(url, decode_responses=True)


def _channel_name(call_id: int) -> str:
    return f"{CHANNEL_PREFIX}{call_id}"


def signal_call_done(call_id: int, status: Optional[str] = None) -> None:
    try:
        client = _redis_client()
        channel = _channel_name(call_id)

        payload = json.dumps({
            "call_id": int(call_id),
            "status": status,
        })

        client.publish(channel, payload)
        client.setex(channel, 300, payload)

    except Exception as exc:
        print(f"call_signal publish failed: call_id={call_id} error={exc}")


def get_call_done_cached_payload(call_log_id: int) -> Optional[Dict[str, Any]]:
    try:
        client = _redis_client()
        channel = _channel_name(call_log_id)

        value = client.get(channel)

        if not value:
            return None

        return json.loads(value)

    except Exception as exc:
        print(f"call_signal cache read failed: call_id={call_log_id} error={exc}")
        return None


def wait_call_done_signal(call_log_id: int, timeout_sec: int = 120) -> bool:
    try:
        client = _redis_client()
        channel = _channel_name(call_log_id)

        if client.get(channel):
            return True

        pubsub = client.pubsub()
        pubsub.subscribe(channel)

        deadline = time.time() + max(1, int(timeout_sec or 1))

        try:
            while time.time() < deadline:
                message = pubsub.get_message(timeout=1.0)

                if message and message.get("type") == "message":
                    return True

        finally:
            pubsub.unsubscribe(channel)
            pubsub.close()

    except Exception as exc:
        print(f"call_signal wait failed: call_id={call_log_id} error={exc}")

    return False