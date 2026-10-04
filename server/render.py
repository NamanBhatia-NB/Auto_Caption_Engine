"""
Eclipse caption renderer.

The renderer mirrors the React caption overlay in client/src/styles.css:
- bundled Montserrat Bold (selected visual match, not claimed exact)
- white normal words
- #FFE000 active word
- rgba(255, 212, 71, 0.20) active-word background
- identical font-space advances in the browser and ASS
- caption positioned at 15.6% from the bottom
- 3.9% left/right margins
"""
from __future__ import annotations

import os
import json
import re
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from matting import MattingUnavailable, ensure_person_mask


@dataclass(frozen=True)
class RenderResult:
    output_path: str
    matting_applied: bool
    matting_cached: bool
    warning: str | None = None


# One style source for CSS-em sizes, colors, and video-relative placement.
STYLE_PATH = Path(__file__).resolve().parent.parent / "client/src/caption-style.json"
ECLIPSE_STYLE: dict[str, Any] = json.loads(STYLE_PATH.read_text(encoding="utf-8"))

# Metrics from the bundled Montserrat-Bold.ttf (1000 units per em).
# libass sizes fonts by OS/2 Win ascent + descent, CSS sizes by unitsPerEm.
# This conversion replaces the former guessed 1.15 multiplier.
ASS_EM_RATIO = (1109 + 453) / 1000
WIN_DESCENT_EM = 453 / 1000
CSS_ASCENT_EM = 968 / 1000
CSS_DESCENT_EM = 251 / 1000


def merge_style(style: dict | None = None) -> dict[str, Any]:
    """Return a validated, safe style configuration."""
    merged = dict(ECLIPSE_STYLE)
    if isinstance(style, dict):
        merged.update({key: value for key, value in style.items() if value is not None})

    merged["font_name"] = _safe_font_name(merged["font_name"])
    merged["font_size"] = _clamp_int(merged.get("font_size"), 28, 120, ECLIPSE_STYLE["font_size"])
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

    css_size = width * s["font_size"] / ECLIPSE_STYLE["reference_width"]
    font_size = round(css_size * ASS_EM_RATIO, 3)

    # React:
    #   bottom: 15.6%
    #   left: 3.9%
    #   right: 3.9%
    # Match the CSS baseline inside line-height:1.12, rather than moving the
    # visible letters upward when ASS's larger Win line box is used.
    css_baseline_from_bottom = css_size * (
        ECLIPSE_STYLE["line_height"] - CSS_ASCENT_EM + CSS_DESCENT_EM
    ) / 2
    margin_bottom = max(round(
        height * ECLIPSE_STYLE["bottom_ratio"]
        + css_baseline_from_bottom - css_size * WIN_DESCENT_EM
    ), 1)
    margin_lr = max(round(width * ECLIPSE_STYLE["side_margin_ratio"]), 1)
    box_padding = round(css_size * 0.065, 3)

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
WrapStyle: 1
ScaledBorderAndShadow: yes
YCbCr Matrix: TV.601

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,{s['font_name']},{font_size},{primary},&H000000FF,&H00000000,&H00000000,1,0,0,0,100,100,0,0,1,0,0,2,{margin_lr},{margin_lr},{margin_bottom},1
Style: Active,{s['font_name']},{font_size},{highlight},&H000000FF,{highlight_box},{highlight_box},1,0,0,0,100,100,0,0,3,{box_padding},0,2,{margin_lr},{margin_lr},{margin_bottom},1

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
    *,
    matting_cache_path: str | None = None,
    progress: Callable[[int, str], None] | None = None,
) -> RenderResult:
    """Render normal captions; optionally composite foreground with RVM.

    If local RVM matting cannot run, the function deliberately falls back to
    the existing normal ASS burn and returns the reason in ``warning``.
    """
    ass_content = generate_eclipse_ass(
        segments,
        video_width,
        video_height,
        style,
    )

    ass_path = ""
    output_directory = os.path.dirname(output_path)
    os.makedirs(output_directory, exist_ok=True)
    font_directory = (
        Path(__file__).resolve().parent.parent / "client" / "public" / "fonts"
    )
    mask_path = matting_cache_path or str(Path(output_directory) / "person-mask.mkv")
    matting_applied = False
    matting_cached = False
    warning = None

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
        # Temporarily opt-in: normal captions stay fully visible by default.
        if os.getenv("RVM_ENABLED", "false").strip().lower() in {
            "1", "true", "yes", "on"
        }:
            try:
                matte = ensure_person_mask(
                    video_path,
                    mask_path,
                    progress=progress,
                )
                mask_path = matte.mask_path
                matting_applied = True
                matting_cached = matte.cached
            except MattingUnavailable as error:
                warning = (
                    "Behind-person matting was unavailable; rendered normal captions instead. "
                    + str(error)
                )

        if progress:
            progress(82, "compositing captions behind foreground" if matting_applied else "rendering captions")
        subtitle_filter_path = _escape_filter_path(ass_path)
        fonts_filter_path = _escape_filter_path(str(font_directory))
        ass_filter = (
            f"ass=filename='{subtitle_filter_path}':fontsdir='{fonts_filter_path}'"
        )

        if matting_applied:
            filter_graph = (
                "[0:v]setpts=PTS-STARTPTS,split=2[original][caption_base];"
                f"[caption_base]{ass_filter}[captioned];"
                "[1:v]setpts=PTS-STARTPTS,format=gray[matte];"
                "[captioned][original][matte]maskedmerge,format=yuv420p[outv]"
            )
            video_inputs = ["-i", video_path, "-i", mask_path]
            video_filter = ["-filter_complex", filter_graph, "-map", "[outv]"]
        else:
            video_inputs = ["-i", video_path]
            video_filter = ["-vf", ass_filter, "-map", "0:v:0"]

        command = [
            "ffmpeg", "-hide_banner", "-y",
            *video_inputs,
            *video_filter,
            "-map", "0:a?",
            "-c:v", "libx264",
            "-preset", os.getenv("FFMPEG_PRESET", "medium"),
            "-crf", os.getenv("FFMPEG_CRF", "21"),
            "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "128k",
            "-movflags", "+faststart",
            output_path,
        ]

        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=int(os.getenv("RENDER_TIMEOUT_SECONDS", "1800")),
        )

        if result.returncode != 0:
            raise RuntimeError(
                f"FFmpeg rendering failed: {result.stderr[-1800:]}"
            )

        if progress:
            progress(100, "complete")
        return RenderResult(output_path, matting_applied, matting_cached, warning)

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
      - bundled Montserrat Bold
      - normal text: #FFFFFF
      - active text: #FFE000
      - active box: #393117 (visual result of rgba(255,212,71,0.2)
        over the reference #08090B background)
      - proportional active-box padding (does not change text advances)
      - normal font spaces, matching browser inline text
    """
    rendered_words: list[str] = []

    gap = " "

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
    value = str(value or "Montserrat").strip()

    # Font names are inserted into an ASS header, so keep them single-line.
    value = re.sub(r"[^A-Za-z0-9 ._-]", "", value)

    return value[:80] or "Montserrat"


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
