"""Free transcription providers used by Auto Caption Engine.

The default path is local Faster-Whisper. It downloads a CTranslate2 model on
first use, preserves real word timestamps, and needs no account or API key.
Groq is kept as an optional server-side provider; credentials are read from the
environment/request and never stored.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
import threading
from pathlib import Path
from typing import Any

import requests

_whisper_model_cache: dict[tuple[str, str, str], Any] = {}
_whisper_model_lock = threading.Lock()
DEFAULT_FASTER_WHISPER_MODEL = "base"

GROQ_URL = "https://api.groq.com/openai/v1/audio/transcriptions"
# Kept for compatibility with the older FFmpeg whisper.cpp fallback path.
DEFAULT_MODEL = "ggml-small.bin"
MODEL_URL = "https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-small.bin?download=true"

def transcribe_with_faster_whisper(
    audio_or_video_path: str,
    language: str = "auto",
    model_size: str | None = None,
) -> dict:
    """Transcribe locally with faster-whisper and preserve real word timings."""

    try:
        from faster_whisper import WhisperModel
    except ImportError as exc:
        raise RuntimeError(
            "faster-whisper is not installed. "
            "Run: python -m pip install faster-whisper"
        ) from exc

    model_name = (model_size or os.getenv(
        "WHISPER_MODEL_SIZE",
        DEFAULT_FASTER_WHISPER_MODEL,
    )).strip()

    device = os.getenv(
        "WHISPER_DEVICE",
        "cpu",
    ).strip().lower()

    compute_type = os.getenv(
        "WHISPER_COMPUTE_TYPE",
        "int8" if device == "cpu" else "float16",
    ).strip()

    cache_key = (model_name, device, compute_type)

    model = _whisper_model_cache.get(cache_key)

    if model is None:
        # Flask can receive more than one request while the first model is
        # loading. Serialize construction so the model is downloaded/loaded
        # once rather than duplicated in memory and CPU work.
        with _whisper_model_lock:
            model = _whisper_model_cache.get(cache_key)
            if model is None:
                try:
                    model = WhisperModel(
                        model_name,
                        device=device,
                        compute_type=compute_type,
                        download_root=os.getenv("WHISPER_DOWNLOAD_ROOT") or None,
                    )
                except Exception as exc:
                    raise RuntimeError(
                        f"Could not load faster-whisper model "
                        f"'{model_name}' on {device}/{compute_type}: {exc}"
                    ) from exc
                _whisper_model_cache[cache_key] = model

    requested_language = (
        None
        if not language or language == "auto"
        else language
    )

    try:
        segments_iter, info = model.transcribe(
            audio_or_video_path,

            language=requested_language,
            task="transcribe",

            beam_size=max(int(os.getenv("WHISPER_BEAM_SIZE", "1")), 1),
            word_timestamps=True,
            vad_filter=os.getenv("WHISPER_VAD", "false").lower() in {
                "1", "true", "yes", "on",
            },
            condition_on_previous_text=os.getenv(
                "WHISPER_CONDITION_ON_PREVIOUS_TEXT", "false"
            ).lower() in {"1", "true", "yes", "on"},
            temperature=0.0,
        )

        segments = []
        all_words = []

        for raw_segment in segments_iter:
            segment_words = []

            for raw_word in raw_segment.words or []:
                value = str(raw_word.word or "").strip()

                if not value:
                    continue

                start = max(
                    float(raw_word.start),
                    0.0,
                )

                end = max(
                    float(raw_word.end),
                    start + 0.01,
                )

                word = {
                    "word": value,
                    "start": round(start, 3),
                    "end": round(end, 3),
                }

                segment_words.append(word)
                all_words.append(word)

            text = str(
                raw_segment.text or ""
            ).strip()

            if segment_words:
                segments.extend(
                    _group_words_into_segments(
                        segment_words
                    )
                )

            elif text:
                segments.append({
                    "text": text,
                    "start": round(
                        float(raw_segment.start),
                        3,
                    ),
                    "end": round(
                        float(raw_segment.end),
                        3,
                    ),
                    "words": [],
                })

        if not segments:
            raise ValueError(
                "No speech was detected in the audio."
            )

        return {
            "text": " ".join(
                segment["text"]
                for segment in segments
            ),

            "language": getattr(
                info,
                "language",
                language,
            ),

            "words": all_words,
            "segments": segments,

            "provider": "faster-whisper",
            "model": model_name,
            "device": device,
            "compute_type": compute_type,
        }

    except Exception as exc:
        if isinstance(exc, ValueError):
            raise

        raise RuntimeError(
            f"faster-whisper transcription failed: {exc}"
        ) from exc

def transcribe_with_groq(audio_path: str, api_key: str, language: str = "auto") -> dict:
    """Transcribe through Groq's free tier, returning word timestamps."""
    if not api_key or not api_key.strip():
        raise ValueError("A Groq API key is required for the optional Groq mode.")

    headers = {"Authorization": f"Bearer {api_key.strip()}"}
    data: dict[str, str] = {
        "model": os.getenv("GROQ_MODEL", "whisper-large-v3-turbo"),
        "response_format": "verbose_json",
        "timestamp_granularities[]": "word",
    }
    if language and language != "auto":
        data["language"] = language

    with open(audio_path, "rb") as audio:
        files = {"file": (os.path.basename(audio_path), audio, "audio/mpeg")}
        response = requests.post(GROQ_URL, headers=headers, files=files, data=data, timeout=180)

    if response.status_code != 200:
        # Do not echo the request headers/key in an error message.
        raise RuntimeError(f"Groq API error {response.status_code}: {response.text[:500]}")

    result = response.json()
    words = []
    for raw_word in result.get("words", []) or []:
        value = str(raw_word.get("word", "")).strip()
        if not value:
            continue
        words.append({
            "word": value,
            "start": round(float(raw_word.get("start", 0)), 3),
            "end": round(float(raw_word.get("end", raw_word.get("start", 0))), 3),
        })

    segments = _group_words_into_segments(words)
    if not segments and result.get("text"):
        # Some free-tier responses may omit word timestamps.  Preserve a useful
        # caption rather than returning a blank render.
        segments = _fallback_text_segment(str(result["text"]), 0, float(result.get("duration", 1) or 1))
        words = [word for segment in segments for word in segment["words"]]

    return {
        "text": str(result.get("text", "")),
        "language": result.get("language", language),
        "words": words,
        "segments": segments,
    }


def transcribe_with_ffmpeg_whisper_srt(
    video_path: str,
    model_path: str | None = None,
    language: str = "auto",
) -> dict:
    """Transcribe locally with FFmpeg's whisper.cpp filter and parse SRT."""
    model_path = model_path or _ensure_whisper_model()
    if not os.path.exists(model_path):
        raise FileNotFoundError(f"Whisper model not found: {model_path}")

    with tempfile.NamedTemporaryFile(suffix=".srt", delete=False) as subtitle_file:
        srt_path = subtitle_file.name

    try:
        model = _escape_filter_value(model_path)
        destination = _escape_filter_value(srt_path)
        filter_expr = (
            f"whisper=model='{model}':language={_safe_language(language)}:"
            f"format=srt:max_len=30:destination='{destination}'"
        )
        command = [
            "ffmpeg", "-hide_banner", "-y",
            "-i", video_path,
            "-vn", "-sn", "-dn",
            "-af", filter_expr,
            "-f", "null", "-",
        ]
        result = subprocess.run(command, capture_output=True, text=True, timeout=900)
        if result.returncode != 0:
            raise RuntimeError(f"Local Whisper failed: {result.stderr[-1800:]}")
        if not os.path.exists(srt_path):
            raise RuntimeError("Local Whisper completed without producing subtitles.")
        with open(srt_path, "r", encoding="utf-8-sig") as subtitle_file:
            parsed = _parse_srt_to_segments(subtitle_file.read())
        if not parsed["segments"]:
            raise RuntimeError("No speech was detected in the audio.")
        return parsed
    finally:
        if os.path.exists(srt_path):
            os.unlink(srt_path)


def _ensure_whisper_model(model_name: str = DEFAULT_MODEL) -> str:
    """Download the open small model once into server/models."""
    configured = os.getenv("WHISPER_MODEL_PATH")
    if configured:
        return configured

    models_dir = Path(__file__).parent / "models"
    models_dir.mkdir(parents=True, exist_ok=True)
    model_path = models_dir / model_name
    if model_path.exists() and model_path.stat().st_size > 1024:
        return str(model_path)

    temporary_path = model_path.with_suffix(model_path.suffix + ".part")
    try:
        with requests.get(MODEL_URL, stream=True, timeout=60) as response:
            response.raise_for_status()
            with open(temporary_path, "wb") as model_file:
                for chunk in response.iter_content(chunk_size=1024 * 1024):
                    if chunk:
                        model_file.write(chunk)
        temporary_path.replace(model_path)
    except Exception:
        if temporary_path.exists():
            temporary_path.unlink()
        raise RuntimeError(
            "Could not download the free Whisper model. Check your internet connection "
            f"or set WHISPER_MODEL_PATH in .env. Source: {MODEL_URL}"
        )
    return str(model_path)


def _parse_srt_to_segments(srt_content: str) -> dict:
    """Parse SRT captions and approximate word timings within each cue."""
    pattern = re.compile(
        r"(?:^|\n)\s*\d+\s*\n"
        r"(\d{2}:\d{2}:\d{2}[,.]\d{3})\s*-->\s*(\d{2}:\d{2}:\d{2}[,.]\d{3}).*?\n"
        r"(.*?)(?=\n\s*\n|\Z)",
        re.DOTALL,
    )
    segments = []
    words = []
    for match in pattern.finditer(srt_content):
        start = _srt_time_to_seconds(match.group(1))
        end = max(_srt_time_to_seconds(match.group(2)), start + 0.05)
        text = re.sub(r"\s+", " ", match.group(3).strip())
        if not text:
            continue
        segment = _fallback_text_segment(text, start, end)[0]
        segments.append(segment)
        words.extend(segment["words"])

    return {
        "text": " ".join(segment["text"] for segment in segments),
        "language": "auto",
        "words": words,
        "segments": segments,
    }


def _group_words_into_segments(
    words: list[dict], max_words: int = 5, max_duration: float = 3.0
) -> list[dict]:
    """Group timestamped words into readable 2-5 word Eclipse cues."""
    segments = []
    current = []
    for word in words:
        if not word.get("word"):
            continue
        current.append(word)
        reaches_limit = len(current) >= max_words
        reaches_duration = current and word["end"] - current[0]["start"] >= max_duration
        if reaches_limit or reaches_duration:
            segments.append(_make_segment(current))
            current = []
    if current:
        segments.append(_make_segment(current))
    return segments


def retime_segment_text(text: str, start: float, end: float) -> dict:
    """Create editable word timings for a changed caption cue."""
    return _fallback_text_segment(text, start, end)[0]


def _fallback_text_segment(text: str, start: float, end: float) -> list[dict]:
    values = [part for part in str(text).split() if part]
    if not values:
        return []
    start = max(float(start), 0.0)
    end = max(float(end), start + 0.05)
    duration = end - start
    per_word = duration / len(values)
    words = []
    for index, value in enumerate(values):
        words.append({
            "word": value,
            "start": round(start + index * per_word, 3),
            "end": round(start + (index + 1) * per_word, 3),
        })
    return [{"text": " ".join(values), "start": start, "end": end, "words": words}]


def _make_segment(words: list[dict]) -> dict:
    return {
        "text": " ".join(str(word["word"]).strip() for word in words),
        "start": round(float(words[0]["start"]), 3),
        "end": round(float(words[-1]["end"]), 3),
        "words": words[:],
    }


def _parse_timestamp(timestamp: Any) -> float:
    if isinstance(timestamp, (int, float)):
        return float(timestamp)
    value = str(timestamp or "0").strip().replace(",", ".")
    parts = value.split(":")
    if len(parts) == 3:
        return float(parts[0]) * 3600 + float(parts[1]) * 60 + float(parts[2])
    if len(parts) == 2:
        return float(parts[0]) * 60 + float(parts[1])
    return float(value)


def _srt_time_to_seconds(timestamp: str) -> float:
    return _parse_timestamp(timestamp)


def _safe_language(language: str) -> str:
    language = str(language or "auto").strip().lower()
    return language if re.fullmatch(r"[a-z]{2,5}", language) else "auto"


def _escape_filter_value(value: str) -> str:
    return str(value).replace("\\", "\\\\").replace(":", r"\:").replace("'", r"\'")


# Kept for callers that used the earlier JSON helper.
def _parse_ffmpeg_whisper_json(content: str) -> dict:
    try:
        payload = json.loads(content)
    except json.JSONDecodeError:
        payload = {"transcription": [json.loads(line) for line in content.splitlines() if line.strip()]}
    transcription = payload if isinstance(payload, list) else payload.get("transcription", [])
    words = []
    for segment in transcription:
        text = str(segment.get("text", "")).strip()
        timestamps = segment.get("timestamps", segment.get("offsets", {}))
        words.extend(_fallback_text_segment(
            text,
            _parse_timestamp(timestamps.get("from", 0)),
            _parse_timestamp(timestamps.get("to", 0)),
        )[0]["words"] if text else [])
    return {"text": " ".join(word["word"] for word in words), "language": "auto", "words": words, "segments": _group_words_into_segments(words)}
