# app/celery_app.py

from __future__ import annotations

from celery import Celery

from app.config import settings


celery_app = Celery(
    "voice_crm",
    broker=settings.REDIS_URL,
    backend=settings.REDIS_URL,
    include=[
        "app.tasks.campaign_tasks",
    ],
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",

    timezone="UTC",
    enable_utc=True,

    task_track_started=True,
    result_expires=3600,

    # Important for production/restart stability
    broker_connection_retry_on_startup=True,

    # Important for voice campaign system:
    # do not let one worker reserve many campaign tasks at once.
    worker_prefetch_multiplier=1,

    # Keep one campaign task safe per worker process.
    task_acks_late=False,
)