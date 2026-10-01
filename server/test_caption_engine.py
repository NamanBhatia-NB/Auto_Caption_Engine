import sys
import unittest

sys.path.insert(0, str(__import__('pathlib').Path(__file__).parent))

from render import generate_eclipse_ass, merge_style
from transcribe import (
    DEFAULT_FASTER_WHISPER_MODEL,
    DEFAULT_MODEL,
    _parse_srt_to_segments,
    retime_segment_text,
)


class CaptionEngineTests(unittest.TestCase):
    def test_srt_parser_creates_word_level_timings(self):
        result = _parse_srt_to_segments(
            "1\n00:00:01,000 --> 00:00:03,000\nHello Eclipse world\n"
        )
        self.assertEqual(result["text"], "Hello Eclipse world")
        self.assertEqual(len(result["words"]), 3)
        self.assertAlmostEqual(result["words"][0]["start"], 1.0)
        self.assertAlmostEqual(result["words"][-1]["end"], 3.0)

    def test_eclipse_ass_uses_native_frame_safe_wrapping(self):
        segment = retime_segment_text("hello eclipse world again this caption needs wrapping", 0, 2)
        ass = generate_eclipse_ass([segment], style={"uppercase": True})
        self.assertEqual(ass.count("Dialogue:"), 8)
        self.assertIn("\\c&H0000E0FF", ass)
        self.assertIn("\\3c&H3AA0F0FF", ass)
        self.assertNotIn("\\fad(70,50)", ass)
        self.assertNotIn(chr(92) + "N", ass)
        self.assertIn("Style: Default,Arial,52", ass)
        self.assertIn("HELLO", ass)

    def test_default_style_keeps_case_and_uses_word_end_boundaries(self):
        segment = {
            "text": "hello world",
            "start": 1,
            "end": 3,
            "words": [
                {"word": "hello", "start": 1, "end": 1.4},
                {"word": "world", "start": 2, "end": 2.5},
            ],
        }
        ass = generate_eclipse_ass([segment])
        dialogue_lines = [line for line in ass.splitlines() if line.startswith("Dialogue:")]
        self.assertIn("Dialogue: 0,0:00:01.00,0:00:01.40", dialogue_lines[0])
        self.assertIn("Dialogue: 0,0:00:02.00,0:00:02.50", dialogue_lines[1])
        self.assertIn("hello{\\r}", dialogue_lines[0])
        self.assertNotIn("HELLO", ass)

    def test_transcription_defaults_are_fast_and_legacy_fallback_remains_available(self):
        self.assertEqual(DEFAULT_FASTER_WHISPER_MODEL, "base")
        self.assertEqual(DEFAULT_MODEL, "ggml-small.bin")

    def test_style_values_are_clamped_and_font_name_is_safe(self):
        style = merge_style({"font_size": 999, "margin_bottom": -1, "font_name": "Arial,evil\\tag"})
        self.assertEqual(style["font_size"], 120)
        self.assertEqual(style["margin_bottom"], 40)
        self.assertNotIn("\\", style["font_name"])
        self.assertNotIn(",", style["font_name"])


if __name__ == "__main__":
    unittest.main()
