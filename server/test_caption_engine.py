import sys
import unittest

sys.path.insert(0, str(__import__('pathlib').Path(__file__).parent))

from render import generate_eclipse_ass, merge_style
from transcribe import DEFAULT_FASTER_WHISPER_MODEL, retime_segment_text


class CaptionEngineTests(unittest.TestCase):
    def test_eclipse_ass_uses_native_frame_safe_wrapping(self):
        segment = retime_segment_text("hello eclipse world again this caption needs wrapping", 0, 2)
        ass = generate_eclipse_ass([segment], style={"uppercase": True})
        self.assertEqual(ass.count("Dialogue:"), 8)
        self.assertIn("\\c&H0000E0FF", ass)
        self.assertNotIn("\\3c", ass)
        self.assertNotIn("\\bord", ass)
        self.assertNotIn("\\shad", ass)
        self.assertNotIn("\\fad", ass)
        self.assertNotIn(chr(92) + "N", ass)
        self.assertIn("Style: Default,Arial,52", ass)
        self.assertIn("Style: Default,Arial,52,&H00FFFFFF,&H000000FF,&H00000000,&H00000000,0,0,0,0", ass)
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

    def test_transcription_defaults_to_fast_faster_whisper_model(self):
        self.assertEqual(DEFAULT_FASTER_WHISPER_MODEL, "base")

    def test_style_values_are_clamped_and_font_name_is_safe(self):
        style = merge_style({"font_size": 999, "margin_bottom": -1, "font_name": "Arial,evil\\tag"})
        self.assertEqual(style["font_size"], 120)
        self.assertEqual(style["margin_bottom"], 40)
        self.assertNotIn("\\", style["font_name"])
        self.assertNotIn(",", style["font_name"])


if __name__ == "__main__":
    unittest.main()
