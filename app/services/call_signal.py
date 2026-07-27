# app/services/call_signal.py

from __future__ import annotations

import json
import time
from typing import Optional

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


def wait_call_done_signal(call_log_id: int, timeout_sec: int = 120) -> bool:
    """Block until run_listener reports this call finished.

    Currently unused: the campaign scheduler polls call status from the database
    instead. Kept as the read side of signal_call_done, which run_listener still
    publishes on every hangup.
    """
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