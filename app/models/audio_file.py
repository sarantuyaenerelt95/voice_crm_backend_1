# app/models/audio_file.py

from __future__ import annotations

import enum

from sqlalchemy import Column, Integer, String, Boolean, DateTime, ForeignKey, Float, Enum, Text, BigInteger
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from app.database import Base


class AudioSource(str, enum.Enum):
    tts = "tts"        # generated from text via edge-tts
    upload = "upload"  # uploaded WAV file


class AudioFile(Base):
    __tablename__ = "audio_files"

    id = Column(Integer, primary_key=True, index=True)
    company_id = Column(Integer, ForeignKey("companies.id"), nullable=False, index=True)
    filename = Column(String(200), nullable=False)       # message_1781422598.wav
    file_path = Column(String(500), nullable=False)      # full path on disk
    source = Column(Enum(AudioSource), nullable=False)
    tts_text = Column(Text)                              # original text if TTS
    duration_sec = Column(Float)                         # audio duration
    file_size_bytes = Column(BigInteger, default=0, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    is_active = Column(Boolean, nullable=False, default=True)

    # relationships
    campaigns = relationship("Campaign", back_populates="audio_file")

    def __repr__(self):
        return f"<AudioFile {self.filename}>"