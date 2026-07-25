# app/services/audio_capacity.py

from __future__ import annotations

import os
from typing import Optional

from fastapi import HTTPException
from sqlalchemy import func

from app.models.audio_file import AudioFile
from app.models.company import Company


MAX_AUDIO_DURATION_SEC = 300  # 5 minutes


def get_company_audio_usage_bytes(db, company_id: int) -> int:
    total = db.query(
        func.coalesce(func.sum(AudioFile.file_size_bytes), 0)
    ).filter(
        AudioFile.company_id == company_id,
        AudioFile.is_active == True,
    ).scalar()

    return int(total or 0)


def get_company_audio_limit_bytes(db, company_id: int) -> int:
    company = db.query(Company).filter(
        Company.id == company_id,
    ).first()

    if not company:
        raise HTTPException(status_code=404, detail="Company not found.")

    limit_mb = int(getattr(company, "audio_storage_limit_mb", 200) or 200)
    return limit_mb * 1024 * 1024


def get_company_audio_usage_summary(db, company_id: int) -> dict:
    used_bytes = get_company_audio_usage_bytes(db, company_id)
    limit_bytes = get_company_audio_limit_bytes(db, company_id)

    return {
        "used_bytes": used_bytes,
        "limit_bytes": limit_bytes,
        "used_mb": round(used_bytes / 1024 / 1024, 2),
        "limit_mb": round(limit_bytes / 1024 / 1024, 2),
        "remaining_bytes": max(limit_bytes - used_bytes, 0),
        "remaining_mb": round(max(limit_bytes - used_bytes, 0) / 1024 / 1024, 2),
    }


def check_audio_duration(duration_sec: Optional[float]):
    duration = float(duration_sec or 0)

    if duration > MAX_AUDIO_DURATION_SEC:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Audio file is too long. "
                f"Maximum allowed duration is 5 minutes, "
                f"but uploaded audio is {duration:.1f} seconds."
            ),
        )


def check_audio_storage_capacity(db, company_id: int, new_file_size_bytes: int):
    company = db.query(Company).filter(
        Company.id == company_id,
    ).first()

    if not company:
        raise HTTPException(status_code=404, detail="Company not found.")

    limit_mb = int(getattr(company, "audio_storage_limit_mb", 200) or 200)
    limit_bytes = limit_mb * 1024 * 1024

    used_bytes = get_company_audio_usage_bytes(db, company_id)

    if used_bytes + int(new_file_size_bytes or 0) > limit_bytes:
        used_mb = used_bytes / 1024 / 1024
        new_mb = int(new_file_size_bytes or 0) / 1024 / 1024

        raise HTTPException(
            status_code=400,
            detail=(
                f"Audio storage limit exceeded. "
                f"Used {used_mb:.1f} MB, new file {new_mb:.1f} MB, "
                f"limit {limit_mb} MB."
            ),
        )


def safe_remove_file(path: Optional[str]):
    try:
        if path and os.path.exists(path):
            os.remove(path)
    except Exception:
        pass