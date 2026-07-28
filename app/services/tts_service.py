# app/services/tts_service.py

from __future__ import annotations

import asyncio
from pathlib import Path

import edge_tts


DEFAULT_TTS_VOICE = "mn-MN-YesuiNeural"


class TTSService:
    @staticmethod
    def generate_speech(text: str, output_path: str, voice: str = DEFAULT_TTS_VOICE) -> None:
        text = (text or "").strip()

        if not text:
            raise ValueError("TTS text is empty.")

        output_file = Path(output_path)
        output_file.parent.mkdir(parents=True, exist_ok=True)

        async def _run():
            communicate = edge_tts.Communicate(text, voice)
            await communicate.save(str(output_file))

        asyncio.run(_run())

        if not output_file.exists() or output_file.stat().st_size <= 0:
            raise RuntimeError("TTS audio file was not created.")
