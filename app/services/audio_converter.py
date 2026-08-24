# app/services/audio_converter.py

from __future__ import annotations

import subprocess
import wave
from pathlib import Path


class AudioConverter:
    @staticmethod
    def _run_command(command: list) -> subprocess.CompletedProcess:
        try:
            return subprocess.run(
                command,
                check=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
        except FileNotFoundError as exc:
            tool_name = command[0] if command else "command"
            raise RuntimeError(
                f"{tool_name} is not installed or not found in PATH."
            ) from exc
        except subprocess.CalledProcessError as exc:
            stderr = (exc.stderr or "").strip()
            stdout = (exc.stdout or "").strip()
            message = stderr or stdout or str(exc)
            raise RuntimeError(message) from exc

    @staticmethod
    def ensure_tools_available() -> None:
        AudioConverter._run_command(["ffmpeg", "-version"])
        AudioConverter._run_command(["ffprobe", "-version"])

    @staticmethod
    def convert_to_wav_8k_mono(input_path: str, output_path: str) -> None:
        input_file = Path(input_path)
        output_file = Path(output_path)

        if not input_file.exists():
            raise FileNotFoundError(f"Input audio file not found: {input_path}")

        output_file.parent.mkdir(parents=True, exist_ok=True)

        command = [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-i",
            str(input_file),
            "-vn",
            "-ac",
            "1",
            "-ar",
            "8000",
            "-acodec",
            "pcm_s16le",
            "-f",
            "wav",
            str(output_file),
        ]

        AudioConverter._run_command(command)

        if not output_file.exists() or output_file.stat().st_size <= 0:
            raise RuntimeError(f"Converted audio file was not created: {output_path}")

    @staticmethod
    def get_duration_sec(path: str) -> float:
        audio_path = Path(path)

        if not audio_path.exists():
            raise FileNotFoundError(f"Audio file not found: {path}")

        try:
            with wave.open(str(audio_path), "rb") as wav_file:
                frames = wav_file.getnframes()
                rate = wav_file.getframerate()

                if rate:
                    return round(frames / float(rate), 2)

        except Exception:
            pass

        command = [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(audio_path),
        ]

        result = AudioConverter._run_command(command)
        duration_text = (result.stdout or "").strip()

        if not duration_text:
            raise RuntimeError(f"Could not read audio duration: {path}")

        return round(float(duration_text), 2)



    @staticmethod
    def trim_to_wav_8k_mono(
        input_path: str,
        output_path: str,
        start_sec: float,
        end_sec: float,
    ) -> None:
        """Cut [start_sec, end_sec) out of an audio file into a fresh 8kHz mono WAV.

        Re-encodes rather than stream-copying so the cut lands exactly on the
        requested boundaries instead of the nearest packet.
        """
        input_file = Path(input_path)
        output_file = Path(output_path)

        if not input_file.exists():
            raise FileNotFoundError(f"Input audio file not found: {input_path}")

        start = max(0.0, float(start_sec))
        end = float(end_sec)

        if end <= start:
            raise ValueError("Trim end must be after trim start.")

        output_file.parent.mkdir(parents=True, exist_ok=True)

        command = [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-i",
            str(input_file),
            "-ss",
            f"{start:.3f}",
            "-to",
            f"{end:.3f}",
            "-vn",
            "-ac",
            "1",
            "-ar",
            "8000",
            "-acodec",
            "pcm_s16le",
            "-f",
            "wav",
            str(output_file),
        ]

        AudioConverter._run_command(command)

        if not output_file.exists() or output_file.stat().st_size <= 0:
            raise RuntimeError(f"Trimmed audio file was not created: {output_path}")

    @staticmethod
    def normalize_volume(input_path: str, output_path: str) -> None:
        """Even out loudness and re-encode to the 8kHz mono WAV Asterisk expects."""
        input_file = Path(input_path)
        output_file = Path(output_path)

        if not input_file.exists():
            raise FileNotFoundError(f"Input audio file not found: {input_path}")

        output_file.parent.mkdir(parents=True, exist_ok=True)

        command = [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-i",
            str(input_file),
            "-vn",
            "-af",
            "loudnorm=I=-16:TP=-1.5:LRA=11",
            "-ac",
            "1",
            "-ar",
            "8000",
            "-acodec",
            "pcm_s16le",
            "-f",
            "wav",
            str(output_file),
        ]

        AudioConverter._run_command(command)

        if not output_file.exists() or output_file.stat().st_size <= 0:
            raise RuntimeError(f"Normalized audio file was not created: {output_path}")