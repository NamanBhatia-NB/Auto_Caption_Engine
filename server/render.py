"""
Eclipse caption renderer.

The renderer mirrors the React caption overlay in client/src/styles.css:
- Arial, bold
- white normal words
- #FFE000 active word
- rgba(255, 212, 71, 0.20) active-word background
- 7px inter-word gap
- caption positioned at 15.6% from the bottom
- 3.9% left/right margins
"""
from __future__ import annotations

import os
import re
import subprocess
import tempfile
from typing import Any  


# These values are the 1080x1920 equivalents of the React CSS.
# React:
#   font: 700 clamp(15px, 4.81cqw, 24px)/1.12 Arial
#   bottom: 15.6%
#   left/right: 3.9%
#   column-gap: 7px
#   active color: #FFE000
#   active background: rgba(255, 212, 71, 0.2)
ECLIPSE_STYLE: dict[str, Any] = {
    "font_name": "Arial",
    "font_size": 52,
    "primary_color": "#FFFFFF",
    "highlight_color": "#FFE000",
    "highlight_background": "#FFD447",
    "background_alpha": 20,
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

    merged["background_alpha"] = _clamp_int(
        merged.get("background_alpha"), 0, 100, 20
    )   

    uppercase = merged.get("uppercase", False)
    if isinstance(uppercase, str):
        uppercase = uppercase.strip().lower() in {"1", "true", "yes", "on"}
    merged["uppercase"] = bool(uppercase)

    for key, fallback in (
        ("primary_color", "#FFFFFF"),
        ("highlight_color", "#FFE000"),
        ("highlight_background", "#FFD447"),
    ):
        merged[key] = _normalise_hex(merged.get(key), fallback)

    return merged


def generate_eclipse_ass(
    segments: list,
    video_width: int = 1080,
    video_height: int = 1920,
    style: dict | None = None,
) -> str:
    """Generate word-timed ASS subtitles using the React caption geometry."""
    s = merge_style(style)

    width = max(int(video_width or 1080), 1)
    height = max(int(video_height or 1920), 1)

    # Exact React CSS:
    #   font-size: clamp(15px, 4.81cqw, 24px)
    # cqw = 1% of the caption container width.
    font_size = max(round(width * 0.0481), 1)

    # React:
    #   bottom: 15.6%
    #   left: 3.9%
    #   right: 3.9%
    margin_bottom = max(round(height * 0.156), 1)
    margin_lr = max(round(width * 0.039), 1)

    primary = _hex_to_ass(s["primary_color"])
    highlight = _hex_to_ass(s["highlight_color"])

    # CSS rgba(255, 212, 71, 0.20)
    # ASS alpha is inverted: 00 = opaque, FF = transparent.
    ass_alpha = round(255 * (1 - s["background_alpha"] / 100))
    highlight_box = _hex_to_ass(
        s["highlight_background"],
        alpha=ass_alpha,
    )

    # BorderStyle=3 uses the Active style's OutlineColour as the rectangular
    # word box in libass. The box color is the exact visual color from the
    # reference React screenshot.
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
Style: Default,{s['font_name']},{font_size},{primary},&H000000FF,&H00000000,&H00000000,1,0,0,0,100,100,0,0,1,0,0,2,{margin_lr},{margin_lr},{margin_bottom},1
Style: Active,{s['font_name']},{font_size},{highlight},&H000000FF,{highlight_box},{highlight_box},1,0,0,0,100,100,0,0,3,2,0,2,{margin_lr},{margin_lr},{margin_bottom},1

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
                end = max(
                    _seconds(segment.get("end", start + 1)),
                    start + 0.05,
                )
                ass += _dialogue(start, end, text)
            continue

        seg_start = _seconds(segment.get("start", words[0]["start"]))
        seg_end = max(
            _seconds(segment.get("end", words[-1]["end"])),
            seg_start + 0.05,
        )

        # React checks each word's own [start, end] range.
        # Keep exactly that behavior in the exported video.
        for index, word in enumerate(words):
            start = max(seg_start, _seconds(word["start"]))
            end = min(
                seg_end,
                max(start + 0.03, _seconds(word["end"])),
            )

            if end <= start:
                continue

            text = _build_event_text(
                words,
                index,
                uppercase=s["uppercase"],
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
    """Burn the React-matched Eclipse captions into the video with FFmpeg."""
    ass_content = generate_eclipse_ass(
        segments,
        video_width,
        video_height,
        style,
    )

    ass_path = ""
    os.makedirs(os.path.dirname(output_path), exist_ok=True)

    with tempfile.NamedTemporaryFile(
        suffix=".ass",
        delete=False,
        mode="w",
        encoding="utf-8",
        newline="\n",
    ) as subtitle_file:
        subtitle_file.write(ass_content)
        ass_path = subtitle_file.name

    try:
        subtitle_filter_path = _escape_filter_path(ass_path)

        command = [
            "ffmpeg",
            "-hide_banner",
            "-y",
            "-i",
            video_path,
            "-map",
            "0:v:0",
            "-map",
            "0:a?",
            "-vf",
            f"ass=filename='{subtitle_filter_path}'",
            "-c:v",
            "libx264",
            "-preset",
            os.getenv("FFMPEG_PRESET", "medium"),
            "-crf",
            os.getenv("FFMPEG_CRF", "21"),
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-b:a",
            "128k",
            "-movflags",
            "+faststart",
            output_path,
        ]

        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=900,
        )

        if result.returncode != 0:
            raise RuntimeError(
                f"FFmpeg rendering failed: {result.stderr[-1800:]}"
            )

        return output_path

    finally:
        if ass_path and os.path.exists(ass_path):
            os.unlink(ass_path)


def _dialogue(start: float, end: float, text: str) -> str:
    return (
        f"Dialogue: 0,{_seconds_to_ass_time(start)},"
        f"{_seconds_to_ass_time(end)},Default,,0,0,0,,{text}\n"
    )


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

        result.append(
            {
                "word": value,
                "start": _seconds(
                    word.get("start", segment.get("start", 0))
                ),
                "end": _seconds(
                    word.get("end", segment.get("end", 0))
                ),
            }
        )

    return result


def _display_word(value: str, uppercase: bool) -> str:
    return value.upper() if uppercase else value


def _build_event_text(
    words: list[dict],
    active_index: int,
    uppercase: bool,
) -> str:
    """
    Render one caption line using the React caption layout.

    React:
      - Arial, bold
      - normal text: #FFFFFF
      - active text: #FFE000
      - active box: #393117 (visual result of rgba(255,212,71,0.2)
        over the reference #08090B background)
      - active padding: 2px
      - column-gap: 7px
    """
    rendered_words: list[str] = []

    # Two thin spaces closely reproduce the small 7px CSS column-gap.
    gap = "\u2009\u2009"

    for word_index, word in enumerate(words):
        raw = _display_word(
            str(word.get("word", "")),
            uppercase,
        )
        value = _ass_escape(raw)

        if not value:
            continue

        if word_index == active_index:
            # Switch only this word to the Active ASS style.
            # Active = yellow text + rectangular 2px box.
            value = (
                "{\\rActive}"
                + value
                + "{\\rDefault}"
            )

        rendered_words.append(value)

    return gap.join(rendered_words)


def _ass_escape(value: str) -> str:
    # ASS uses braces for override tags. User/transcription text must never
    # be able to inject one.
    return (
        value
        .replace("\\", "\\\\")
        .replace("{", "\\{")
        .replace("}", "\\}")
        .replace("\n", " ")
    )


def _escape_filter_path(path: str) -> str:
    # FFmpeg's filter parser treats Windows drive colons and backslashes
    # as syntax, even though the normal process argument is already separate.
    return (
        path
        .replace("\\", "/")
        .replace(":", r"\:")
        .replace("'", r"\'")
    )


def _normalise_hex(value: Any, fallback: str) -> str:
    value = str(value or "").strip().upper()

    if re.fullmatch(r"#[0-9A-F]{6}", value):
        return value

    return fallback


def _hex_to_ass(value: str, alpha: int = 0) -> str:
    value = _normalise_hex(value, "#FFFFFF")[1:]
    red = value[0:2]
    green = value[2:4]
    blue = value[4:6]

    return f"&H{int(alpha):02X}{blue}{green}{red}"


def _safe_font_name(value: Any) -> str:
    value = str(value or "Arial").strip()

    # Font names are inserted into an ASS header, so keep them single-line.
    value = re.sub(r"[^A-Za-z0-9 ._-]", "", value)

    return value[:80] or "Arial"


def _clamp_int(
    value: Any,
    low: int,
    high: int,
    fallback: int,
) -> int:
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
