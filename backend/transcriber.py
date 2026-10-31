"""
transcriber.py — Transcribe audio using Groq's Whisper Large v3.
Returns timestamped segments [{start, end, text}, ...], the full transcript,
the detected language and, for non-English reels, an English translation.
"""
import logging
import os
import shutil
import subprocess
from groq import Groq

from errors import ConfigError

log = logging.getLogger("reelgist")

_client = None


def _get_client() -> Groq:
    global _client
    if _client is None:
        api_key = os.getenv("GROQ_API_KEY")
        if not api_key or api_key == "gsk_your_key_here":
            raise ConfigError(
                "GROQ_API_KEY not set. Add it to backend/.env — "
                "get a free key at https://console.groq.com"
            )
        _client = Groq(api_key=api_key)
    return _client


MAX_UPLOAD_BYTES = 24 * 1024 * 1024  # Groq's limit is 25 MB; keep headroom


def _shrink_if_needed(audio_path: str) -> tuple[str, bool]:
    """Return (path_to_upload, is_temp). Re-encodes to small mono mp3 if too big."""
    if os.path.getsize(audio_path) <= MAX_UPLOAD_BYTES:
        return audio_path, False

    if not shutil.which("ffmpeg"):
        raise ValueError(
            "Audio is over Groq's 25 MB upload limit and ffmpeg isn't installed "
            "to compress it. Install it with: winget install ffmpeg"
        )

    small = os.path.splitext(audio_path)[0] + ".small.mp3"
    try:
        proc = subprocess.run(
            ["ffmpeg", "-y", "-i", audio_path, "-vn", "-ac", "1", "-ar", "16000",
             "-b:a", "32k", small],
            capture_output=True, text=True, errors="replace", timeout=300,
        )
        if proc.returncode != 0:
            tail = "\n".join(proc.stderr.strip().splitlines()[-5:])
            raise RuntimeError(f"ffmpeg failed to compress audio:\n{tail}")
        if os.path.getsize(small) > MAX_UPLOAD_BYTES:
            raise ValueError(
                "Reel is too long to transcribe (over Groq's 25 MB limit even after compression)."
            )
    except BaseException:
        # Own cleanup of the partial output; the caller never sees this path
        if os.path.exists(small):
            os.remove(small)
        raise
    return small, True


_ENGLISH = {"en", "english"}


def is_english(language: str | None) -> bool:
    # Unknown language is treated as English so we never translate needlessly
    return not language or language.strip().lower() in _ENGLISH


def transcribe(audio_path: str) -> dict:
    """
    Returns:
        {
          "text": "full transcript string (original language)",
          "segments": [{"start": 0.0, "end": 3.4, "text": "Hello..."}, ...],
          "language": "hindi" | "english" | ... | None,
          "translation": {"text": ..., "segments": [...]} or None  (English, non-English reels only)
        }
    """
    client = _get_client()
    upload_path, is_temp = _shrink_if_needed(audio_path)
    try:
        result = _transcribe_file(client, upload_path)
        result["translation"] = None
        if not is_english(result["language"]):
            try:
                result["translation"] = _translate_file(client, upload_path)
            except Exception as e:
                # The original transcript is still useful; don't fail the whole reel
                log.warning(f"English translation failed ({e}); keeping original transcript only")
        return result
    finally:
        if is_temp and os.path.exists(upload_path):
            os.remove(upload_path)


def _transcribe_file(client, audio_path: str) -> dict:
    with open(audio_path, "rb") as f:
        result = client.audio.transcriptions.create(
            file=(os.path.basename(audio_path), f),
            model="whisper-large-v3",
            response_format="verbose_json",  # gives timestamps per segment
            temperature=0.0,
        )
    parsed = _parse_verbose(result)
    language = getattr(result, "language", None)
    if language is None and isinstance(result, dict):
        language = result.get("language")
    parsed["language"] = language.strip().lower() if isinstance(language, str) and language.strip() else None
    return parsed


def _translate_file(client, audio_path: str) -> dict:
    # Whisper's translation endpoint always outputs English, with its own timestamps
    with open(audio_path, "rb") as f:
        result = client.audio.translations.create(
            file=(os.path.basename(audio_path), f),
            model="whisper-large-v3",
            response_format="verbose_json",
            temperature=0.0,
        )
    return _parse_verbose(result)


def _parse_verbose(result) -> dict:
    # verbose_json returns an object with .text and .segments
    segments = []
    raw_segments = getattr(result, "segments", None) or []
    for seg in raw_segments:
        if hasattr(seg, "start"):
            segments.append({
                "start": round(seg.start, 1),
                "end": round(seg.end, 1),
                "text": seg.text.strip(),
            })
        elif isinstance(seg, dict):
            segments.append({
                "start": round(seg.get("start", 0), 1),
                "end": round(seg.get("end", 0), 1),
                "text": seg.get("text", "").strip(),
            })

    full_text = result.text if hasattr(result, "text") else " ".join(
        s["text"] for s in segments
    )

    return {"text": full_text, "segments": segments}

