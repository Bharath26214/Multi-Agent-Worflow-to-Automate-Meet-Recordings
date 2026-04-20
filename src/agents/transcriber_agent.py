from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Iterable

from openai import OpenAI
from openai import BadRequestError

from utils.logger import get_logger

logger = get_logger(__name__)


class TranscriberAgent:
    """Agent that transcribes meeting audio files into text."""

    def __init__(self, model: str | None = None):
        self.model = model or os.getenv("OPENAI_TRANSCRIBE_MODEL", "gpt-4o-mini-transcribe")
        self.timestamp_fallback_model = os.getenv(
            "OPENAI_TRANSCRIBE_TIMESTAMP_MODEL", "whisper-1"
        )

    @staticmethod
    def _format_seconds_to_mmss(seconds: float) -> str:
        total = max(0, int(round(seconds)))
        mm = total // 60
        ss = total % 60
        return f"{mm:02d}:{ss:02d}"

    @classmethod
    def _format_segments_as_transcript(cls, segments: Iterable[Any]) -> str:
        lines = []
        current_speaker = "Speaker"
        for seg in segments:
            start = float(getattr(seg, "start", 0.0) or 0.0)
            text = (getattr(seg, "text", "") or "").strip()
            if not text:
                continue
            speaker, utterance = cls._extract_speaker_and_utterance(text)
            if speaker:
                current_speaker = speaker
            lines.append(
                f"[{cls._format_seconds_to_mmss(start)}] {current_speaker}: {utterance}"
            )
        return "\n".join(lines).strip()

    @staticmethod
    def _extract_speaker_and_utterance(text: str) -> tuple[str | None, str]:
        """
        Convert patterns like:
        - "John says, hello team"
        - "Priya said hello"
        into ("John", "hello team"), etc.
        """
        normalized = text.strip()
        match = re.match(
            r"^\s*([A-Za-z][A-Za-z0-9_-]{1,40})\s+says[,:\s]+(.+)$",
            normalized,
            flags=re.IGNORECASE,
        )
        if match:
            speaker = match.group(1).strip()
            utterance = match.group(2).strip()
            return speaker, utterance
        return None, normalized

    def _transcribe_with_client(self, client: OpenAI, audio_path: Path, model: str):
        with audio_path.open("rb") as audio_file:
            return client.audio.transcriptions.create(
                model=model,
                file=audio_file,
                response_format="verbose_json",
            )

    @staticmethod
    def _ffmpeg_convert_to_pcm_wav(input_path: Path) -> Path:
        ffmpeg_bin = shutil.which("ffmpeg")
        if not ffmpeg_bin:
            raise RuntimeError(
                "ffmpeg is required to auto-convert unsupported audio. "
                "Install ffmpeg and retry."
            )

        temp_dir = Path(tempfile.mkdtemp(prefix="meet_audio_convert_"))
        output_path = temp_dir / "converted.wav"
        cmd = [
            ffmpeg_bin,
            "-y",
            "-i",
            str(input_path),
            "-ac",
            "1",
            "-ar",
            "16000",
            "-c:a",
            "pcm_s16le",
            str(output_path),
        ]
        proc = subprocess.run(cmd, capture_output=True, text=True)
        if proc.returncode != 0 or not output_path.exists():
            raise RuntimeError(
                "Failed to convert audio with ffmpeg. "
                f"stderr: {proc.stderr.strip()[:400]}"
            )
        return output_path

    def transcribe_audio_file(self, recording_file_path: str) -> str:
        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            raise RuntimeError(
                "OPENAI_API_KEY is not set. Please export it before running."
            )

        audio_path = Path(recording_file_path)
        if not audio_path.exists():
            raise FileNotFoundError(f"Recording file not found: {recording_file_path}")
        if audio_path.stat().st_size == 0:
            raise RuntimeError(f"Recording file is empty: {recording_file_path}")

        logger.info("Transcribing recording: %s", str(audio_path))
        client = OpenAI(api_key=api_key)
        try:
            transcript = self._transcribe_with_client(client, audio_path, self.model)
        except BadRequestError as exc:
            message = str(exc).lower()
            # Some models don't support verbose_json. Retry with a timestamp-capable model.
            if "response_format" in message and "not compatible" in message:
                logger.info(
                    "Model %s does not support verbose_json; retrying with %s",
                    self.model,
                    self.timestamp_fallback_model,
                )
                transcript = self._transcribe_with_client(
                    client, audio_path, self.timestamp_fallback_model
                )
            elif "corrupted" in message or "unsupported" in message or "invalid_value" in message:
                logger.info(
                    "Primary transcription failed due to audio format; converting to PCM WAV and retrying"
                )
                converted_path = self._ffmpeg_convert_to_pcm_wav(audio_path)
                try:
                    transcript = self._transcribe_with_client(
                        client, converted_path, self.model
                    )
                except BadRequestError as converted_exc:
                    converted_message = str(converted_exc).lower()
                    if "response_format" in converted_message and "not compatible" in converted_message:
                        logger.info(
                            "Converted retry model %s does not support verbose_json; retrying with %s",
                            self.model,
                            self.timestamp_fallback_model,
                        )
                        transcript = self._transcribe_with_client(
                            client, converted_path, self.timestamp_fallback_model
                        )
                    else:
                        raise
            else:
                raise

        segments = getattr(transcript, "segments", None) or []
        if segments:
            formatted = self._format_segments_as_transcript(segments)
            logger.info("Transcription complete with %d timestamped segments", len(segments))
            return formatted

        text = (getattr(transcript, "text", "") or "").strip()
        logger.info(
            "Transcription complete without segment metadata (%d chars)",
            len(text),
        )
        return text


__all__ = ["TranscriberAgent"]

