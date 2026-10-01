"""
Eclipse caption renderer.

The renderer deliberately keeps the visual treatment in one reusable style
configuration instead of baking the look into a particular video.  It emits
ASS subtitles because libass (which ships with most FFmpeg builds) gives us
precise word timing, text outlines and animated overrides without a paid
video service.
"""

from __future__ import annotations

import os
import re
import subprocess
import tempfile
from typing import Any


# Values are authored for a 1080 x 1920 vertical video and scaled to the input
# resolution by generate_eclipse_ass().  They can be overridden by the UI/API.
ECLIPSE_STYLE: dict[str, Any] = {
    "font_name": "Arial",
    "font_size": 52,
    "primary_color": "#FFFFFF",
    "highlight_color": "#FFE000",
    "margin_bottom": 300,
    "max_words": 5,
    "uppercase": False,
}


def merge_style(style: dict | None = None) -> dict[str, Any]:
    """Return a validated, safe style configuration."""
    merged = dict(ECLIPSE_STYLE)
    if isinstance(style, dict):
        merged.update({key: value for key, value in style.items() if value is not None})

    merged["font_name"] = _safe_font_name(merged["font_name"])
    merged["font_size"] = _clamp_int(merged.get("font_size"), 28, 120, 52)
    merged["margin_bottom"] = _clamp_int(merged.get("margin_bottom"), 40, 700, 300)
    merged["max_words"] = _clamp_int(merged.get("max_words"), 2, 8, 5)
    uppercase = merged.get("uppercase", False)
    if isinstance(uppercase, str):
        uppercase = uppercase.strip().lower() in {"1", "true", "yes", "on"}
    merged["uppercase"] = bool(uppercase)

    for key, fallback in (
        ("primary_color", "#FFFFFF"),
        ("highlight_color", "#FFE000"),
    ):
        merged[key] = _normalise_hex(merged.get(key), fallback)
    return merged


def generate_eclipse_ass(
    segments: list,
    video_width: int = 1080,
    video_height: int = 1920,
    style: dict | None = None,
) -> str:
    """Generate word-timed ASS subtitles in the reusable Eclipse style."""
    s = merge_style(style)
    width = max(int(video_width or 1080), 1)
    height = max(int(video_height or 1920), 1)
    scale = width / 1080.0
    font_size = max(round(s["font_size"] * scale), 22)
    margin_bottom = max(round(s["margin_bottom"] * height / 1920.0), 30)

    primary = _hex_to_ass(s["primary_color"])
    highlight = _hex_to_ass(s["highlight_color"])

    ass = f"""[Script Info]
Title: Eclipse Auto Captions
ScriptType: v4.00+
PlayResX: {width}
PlayResY: {height}
WrapStyle: 2
ScaledBorderAndShadow: yes
YCbCr Matrix: TV.601

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,{s['font_name']},{font_size},{primary},&H000000FF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,0,0,2,42,42,{margin_bottom},1
Style: Active,{s['font_name']},{font_size},{highlight},&H000000FF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,0,0,2,42,42,{margin_bottom},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""

    for segment in segments or []:
        if not isinstance(segment, dict):
            continue
        words = _normalise_words(segment.get("words"), segment)
        if not words:
            text = _ass_escape(str(segment.get("text", "")).strip())
            if text:
                start = _seconds(segment.get("start", 0))
                end = max(_seconds(segment.get("end", start + 1)), start + 0.05)
                ass += _dialogue(start, end, text)
            continue

        seg_start = _seconds(segment.get("start", words[0]["start"]))
        seg_end = max(_seconds(segment.get("end", words[-1]["end"])), seg_start + 0.05)
        # A segment is split into one event per active word.  This gives the
        # characteristic Eclipse left-to-right highlight sweep.
        for index, word in enumerate(words):
            start = max(seg_start, _seconds(word["start"]))
            # Keep each ASS event aligned to the transcribed word's own end.
            # Using the next word's start makes the highlight linger through
            # pauses and can disagree with the browser preview.
            end = min(seg_end, max(start + 0.03, _seconds(word["end"])))
            if end <= start:
                continue

            text = _build_event_text(
                words,
                index,
                uppercase=s["uppercase"],
                highlight=highlight,
            )
            ass += _dialogue(start, end, text)

    return ass


def render_captioned_video(
    video_path: str,
    segments: list,
    output_path: str,
    video_width: int = 1080,
    video_height: int = 1920,
    style: dict | None = None,
) -> str:
    """Burn Eclipse captions into a video with the local FFmpeg binary."""
    ass_content = generate_eclipse_ass(segments, video_width, video_height, style)
    ass_path = ""
    os.makedirs(os.path.dirname(output_path), exist_ok=True)

    with tempfile.NamedTemporaryFile(
        suffix=".ass", delete=False, mode="w", encoding="utf-8", newline="\n"
    ) as subtitle_file:
        subtitle_file.write(ass_content)
        ass_path = subtitle_file.name

    try:
        subtitle_filter_path = _escape_filter_path(ass_path)
        command = [
            "ffmpeg", "-hide_banner", "-y",
            "-i", video_path,
            "-map", "0:v:0",
            "-map", "0:a?",
            "-vf", f"ass=filename='{subtitle_filter_path}'",
            "-c:v", "libx264",
            "-preset", os.getenv("FFMPEG_PRESET", "medium"),
            "-crf", os.getenv("FFMPEG_CRF", "21"),
            "-pix_fmt", "yuv420p",
            "-c:a", "aac",
            "-b:a", "128k",
            "-movflags", "+faststart",
            output_path,
        ]
        result = subprocess.run(command, capture_output=True, text=True, timeout=900)
        if result.returncode != 0:
            raise RuntimeError(f"FFmpeg rendering failed: {result.stderr[-1800:]}")
        return output_path
    finally:
        if ass_path and os.path.exists(ass_path):
            os.unlink(ass_path)


def _dialogue(start: float, end: float, text: str) -> str:
    return f"Dialogue: 0,{_seconds_to_ass_time(start)},{_seconds_to_ass_time(end)},Default,,0,0,0,,{text}\n"


def _normalise_words(words: Any, segment: dict) -> list[dict]:
    if not isinstance(words, list):
        return []
    result = []
    for word in words:
        if not isinstance(word, dict):
            continue
        value = str(word.get("word", "")).strip()
        if not value:
            continue
        result.append({
            "word": value,
            "start": _seconds(word.get("start", segment.get("start", 0))),
            "end": _seconds(word.get("end", segment.get("end", 0))),
        })
    return result


def _display_word(value: str, uppercase: bool) -> str:
    return value.upper() if uppercase else value


def _build_event_text(
    words: list[dict],
    active_index: int,
    uppercase: bool,
    highlight: str,
) -> str:
    """Render a cue and insert the same readable line breaks for every word."""
    rendered_words: list[str] = []
    for word_index, word in enumerate(words):
        raw = _display_word(str(word.get("word", "")), uppercase)
        value = _ass_escape(raw)
        if not value:
            continue
        if word_index == active_index:
            # The reference uses a clean color change only: no border,
            # shadow, glow, or translucent background.
            value = "{\\c" + highlight + "}" + value + "{\\r}"
        rendered_words.append(value)
    return " ".join(rendered_words)


def _ass_escape(value: str) -> str:
    # ASS uses braces for override tags.  User/transcription text must never be
    # able to inject one.
    return value.replace("\\", "\\\\").replace("{", "\\{").replace("}", "\\}").replace("\n", " ")


def _escape_filter_path(path: str) -> str:
    # FFmpeg's filter parser treats Windows drive colons and backslashes as
    # syntax, even though the normal process argument is already separate.
    return path.replace("\\", "/").replace(":", r"\:").replace("'", r"\'")


def _normalise_hex(value: Any, fallback: str) -> str:
    value = str(value or "").strip().upper()
    if re.fullmatch(r"#[0-9A-F]{6}", value):
        return value
    return fallback


def _hex_to_ass(value: str, alpha: int = 0) -> str:
    value = _normalise_hex(value, "#FFFFFF")[1:]
    red, green, blue = value[0:2], value[2:4], value[4:6]
    return f"&H{int(alpha):02X}{blue}{green}{red}"


def _safe_font_name(value: Any) -> str:
    value = str(value or "Arial").strip()
    # Font names are inserted into an ASS header, so keep them single-line.
    value = re.sub(r"[^A-Za-z0-9 ._-]", "", value)
    return value[:80] or "Arial"


def _clamp_int(value: Any, low: int, high: int, fallback: int) -> int:
    try:
        return max(low, min(high, int(float(value))))
    except (TypeError, ValueError):
        return fallback


def _seconds(value: Any) -> float:
    try:
        return max(float(value), 0.0)
    except (TypeError, ValueError):
        return 0.0


def _seconds_to_ass_time(seconds: float) -> str:
    """Convert seconds to ASS time format H:MM:SS.cc."""
    total_cs = max(0, round(float(seconds) * 100))
    h, remainder = divmod(total_cs, 360000)
    m, remainder = divmod(remainder, 6000)
    s, cs = divmod(remainder, 100)
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"
