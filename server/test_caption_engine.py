import sys
import unittest

sys.path.insert(0, str(__import__('pathlib').Path(__file__).parent))

from render import generate_eclipse_ass, merge_style
from transcribe import _parse_srt_to_segments, retime_segment_text


class CaptionEngineTests(unittest.TestCase):
    def test_srt_parser_creates_word_level_timings(self):
        result = _parse_srt_to_segments(
            "1\n00:00:01,000 --> 00:00:03,000\nHello Eclipse world\n"
        )
        self.assertEqual(result["text"], "Hello Eclipse world")
        self.assertEqual(len(result["words"]), 3)
        self.assertAlmostEqual(result["words"][0]["start"], 1.0)
        self.assertAlmostEqual(result["words"][-1]["end"], 3.0)

    def test_eclipse_ass_switches_active_word_and_wraps_long_cues(self):
        segment = retime_segment_text("hello eclipse world again", 0, 2)
        ass = generate_eclipse_ass([segment])
        self.assertEqual(ass.count("Dialogue:"), 4)
        self.assertIn("\\c&H0000E0FF", ass)
        self.assertIn("\\3c&H3AA0F0FF", ass)
        self.assertIn(chr(92) + "N", ass)
        self.assertIn("HELLO", ass)

    def test_style_values_are_clamped_and_font_name_is_safe(self):
        style = merge_style({"font_size": 999, "margin_bottom": -1, "font_name": "Arial,evil\\tag"})
        self.assertEqual(style["font_size"], 120)
        self.assertEqual(style["margin_bottom"], 40)
        self.assertNotIn("\\", style["font_name"])
        self.assertNotIn(",", style["font_name"])


if __name__ == "__main__":
    unittest.main()
