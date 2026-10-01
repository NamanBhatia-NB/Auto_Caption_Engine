"""Free transcription providers used by Auto Caption Engine.

The default path is local Faster-Whisper. It downloads a CTranslate2 model on
first use, preserves real word timestamps, and needs no account or API key.
"""

from __future__ import annotations

import os
import threading
from typing import Any

_whisper_model_cache: dict[tuple[str, str, str], Any] = {}
_whisper_model_lock = threading.Lock()
DEFAULT_FASTER_WHISPER_MODEL = "base"

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
