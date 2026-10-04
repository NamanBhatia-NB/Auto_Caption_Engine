import sys
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(__import__('pathlib').Path(__file__).parent))

from matting import _bounded_size
from render import ECLIPSE_STYLE, generate_eclipse_ass, merge_style, render_captioned_video
from transcribe import DEFAULT_FASTER_WHISPER_MODEL, retime_segment_text


class CaptionEngineTests(unittest.TestCase):
    def test_eclipse_ass_uses_native_frame_safe_wrapping(self):
        segment = retime_segment_text("hello eclipse world again this caption needs wrapping", 0, 2)
        ass = generate_eclipse_ass([segment], style={"uppercase": True})
        self.assertEqual(ass.count("Dialogue:"), 8)
        self.assertIn("{\\rActive}HELLO{\\rDefault}", ass)
        self.assertIn("&HCC47D4FF", ass)
        self.assertIn("Style: Active,Montserrat,121.836,&H0000E0FF,&H000000FF,&HCC47D4FF,&HCC47D4FF", ass)
        self.assertIn("{\\rActive}", ass)
        self.assertNotIn("\\bord8", ass)
        self.assertNotIn("\\shad0", ass)
        self.assertNotIn("\\fad", ass)
        self.assertNotIn(chr(92) + "N", ass)
        self.assertIn("WrapStyle: 1", ass)
        self.assertIn("Style: Default,Montserrat,121.836", ass)
        self.assertIn("Style: Active,Montserrat,121.836", ass)
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
        self.assertIn("{\\rActive}hello{\\rDefault}", dialogue_lines[0])
        self.assertNotIn("HELLO", ass)

    def test_transcription_defaults_to_fast_faster_whisper_model(self):
        self.assertEqual(DEFAULT_FASTER_WHISPER_MODEL, "base")

    def test_shared_style_and_resolution_scaling(self):
        self.assertEqual(ECLIPSE_STYLE["font_size"], 78)
        for width in (720, 1080, 2160):
            ass = generate_eclipse_ass([], width, round(width * 16 / 9))
            default = next(line for line in ass.splitlines() if line.startswith("Style: Default,"))
            size = float(default.split(",")[2])
            # Convert libass Win metrics back to CSS em; physical glyph size
            # must be the same fraction of every video's width.
            self.assertAlmostEqual(size / 1.562 / width, 78 / 1080, places=6)
        custom = generate_eclipse_ass([], style={"font_size": 90})
        self.assertIn("Style: Default,Montserrat,140.58,", custom)

    def test_default_export_never_runs_person_matting(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {}, clear=True):
            with patch("render.ensure_person_mask") as matte, patch("render.subprocess.run") as ffmpeg:
                ffmpeg.return_value = SimpleNamespace(returncode=0, stderr="")
                result = render_captioned_video("input.mp4", [], str(Path(directory) / "output.mp4"))
                matte.assert_not_called()
                self.assertFalse(result.matting_applied)
                self.assertIsNone(result.warning)
                command = ffmpeg.call_args.args[0]
                self.assertIn("-vf", command)
                self.assertNotIn("-filter_complex", command)
                self.assertNotIn("maskedmerge", " ".join(command))

    def test_matting_inference_size_is_bounded_and_aspect_preserving(self):
        width, height = _bounded_size(1080, 1920, 512)
        self.assertLessEqual(max(width, height), 544)
        self.assertEqual(width % 32, 0)
        self.assertEqual(height % 32, 0)
        self.assertGreater(width, 0)
        self.assertGreater(height, 0)

    def test_style_values_are_clamped_and_font_name_is_safe(self):
        style = merge_style({"font_size": 999, "margin_bottom": -1, "font_name": "Arial,evil\\tag"})
        self.assertEqual(style["font_size"], 120)
        self.assertEqual(style["margin_bottom"], 40)
        self.assertNotIn("\\", style["font_name"])
        self.assertNotIn(",", style["font_name"])


if __name__ == "__main__":
    unittest.main()
