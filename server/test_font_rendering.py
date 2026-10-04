"""Check actual libass glyph sizes, not just the ASS Fontsize field."""

from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

import numpy as np

from render import _escape_filter_path, generate_eclipse_ass


@unittest.skipUnless(shutil.which("ffmpeg"), "FFmpeg is needed for glyph rendering")
class FontRenderingTests(unittest.TestCase):
    def test_bundled_font_has_css_sized_glyphs_at_multiple_resolutions(self):
        fonts = Path(__file__).resolve().parent.parent / "client/public/fonts"
        with tempfile.TemporaryDirectory() as directory:
            ass_path = Path(directory) / "test.ass"
            for width, height in ((720, 1280), (1080, 1920)):
                with self.subTest(width=width):
                    # Unhighlighted capitals isolate font sizing from the box.
                    ass_path.write_text(generate_eclipse_ass(
                        [{"text": "MMMMM", "start": 0, "end": 1}], width, height
                    ), encoding="utf-8")
                    command = [
                        "ffmpeg", "-v", "error", "-f", "lavfi", "-i",
                        f"color=black:s={width}x{height}:d=1",
                        "-vf", f"ass=filename='{_escape_filter_path(str(ass_path))}':"
                        f"fontsdir='{_escape_filter_path(str(fonts))}'",
                        "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "rgb24", "pipe:1",
                    ]
                    frame = subprocess.run(command, capture_output=True, check=True, timeout=30)
                    pixels = np.frombuffer(frame.stdout, dtype=np.uint8).reshape(height, width, 3)
                    ink_y, ink_x = np.where(pixels[:, :, 0] > 180)
                    # Montserrat capHeight=700, unitsPerEm=1000.
                    expected_height = 78 * width / 1080 * .7
                    self.assertAlmostEqual(ink_y.max() - ink_y.min() + 1, expected_height, delta=2)
                    self.assertGreater(ink_x.min(), 0)
                    self.assertLess(ink_x.max(), width - 1)
