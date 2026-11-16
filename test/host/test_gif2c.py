"""Host-side unit tests for tools/gif2c.py (needs Pillow, no ESP-IDF).

Run: menu.sh option "Host unit tests", or
  python3 -m unittest discover -s test/host -p 'test_*.py'
"""
import pathlib
import sys
import tempfile
import unittest

from PIL import Image

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "tools"))
import gif2c  # noqa: E402


def make_gif(path, frames, durations):
    frames[0].save(path, save_all=True, append_images=frames[1:], duration=durations, loop=0)


class LoadFramesTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = pathlib.Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_scales_to_fit_and_centers_on_black(self):
        # 20x20 solid red, 2 frames -> fits 8x4 as a 4x4 square in columns 2..5.
        src = self.dir / "red.gif"
        # Frames must differ, or Pillow merges identical frames when saving.
        red = Image.new("RGB", (20, 20), (255, 0, 0))
        red2 = Image.new("RGB", (20, 20), (240, 0, 0))
        make_gif(src, [red, red2], [150, 300])

        frames = gif2c.load_frames(src, 8, 4)

        self.assertEqual([d for _, d in frames], [150, 300])
        for rgb, _ in frames:
            self.assertEqual(len(rgb), 8 * 4 * 3)
            for y in range(4):
                for x in range(8):
                    px = tuple(rgb[(y * 8 + x) * 3:(y * 8 + x) * 3 + 3])
                    if 2 <= x <= 5:
                        self.assertGreater(px[0], 200, (x, y))
                        self.assertLess(px[1] + px[2], 40, (x, y))
                    else:
                        self.assertEqual(px, (0, 0, 0), (x, y))

    def test_missing_duration_uses_default(self):
        src = self.dir / "still.gif"
        Image.new("RGB", (4, 4), (0, 255, 0)).save(src)  # single frame, no duration

        frames = gif2c.load_frames(src, 4, 4)

        self.assertEqual(len(frames), 1)
        self.assertEqual(frames[0][1], gif2c.DEFAULT_DURATION_MS)

    def test_duration_is_clamped(self):
        self.assertEqual(gif2c.clamp_duration(5), gif2c.MIN_DURATION_MS)
        self.assertEqual(gif2c.clamp_duration(999999), gif2c.MAX_DURATION_MS)
        self.assertEqual(gif2c.clamp_duration(None), gif2c.DEFAULT_DURATION_MS)
        self.assertEqual(gif2c.clamp_duration(0), gif2c.DEFAULT_DURATION_MS)
        self.assertEqual(gif2c.clamp_duration(200), 200)

    def test_missing_file_raises(self):
        with self.assertRaises(gif2c.GifError):
            gif2c.load_frames(self.dir / "nope.gif", 64, 32)

    def test_non_gif_raises(self):
        png = self.dir / "image.gif"
        Image.new("RGB", (4, 4)).save(png, format="PNG")
        with self.assertRaises(gif2c.GifError):
            gif2c.load_frames(png, 64, 32)

    def test_garbage_file_raises(self):
        bad = self.dir / "bad.gif"
        bad.write_bytes(b"not an image at all")
        with self.assertRaises(gif2c.GifError):
            gif2c.load_frames(bad, 64, 32)

    def test_too_many_frames_raises(self):
        src = self.dir / "long.gif"
        imgs = [Image.new("RGB", (2, 2), (i, i, i)) for i in range(gif2c.MAX_FRAMES + 1)]
        make_gif(src, imgs, [100] * len(imgs))
        with self.assertRaises(gif2c.GifError):
            gif2c.load_frames(src, 64, 32)

    def test_invalid_target_size_raises(self):
        src = self.dir / "ok.gif"
        Image.new("RGB", (4, 4)).save(src)
        with self.assertRaises(gif2c.GifError):
            gif2c.load_frames(src, 0, 32)


class RenderCppTest(unittest.TestCase):
    def test_render_contains_frames_and_size(self):
        frames = [(bytes([1, 2, 3, 4, 5, 6]), 120), (bytes([7, 8, 9, 10, 11, 12]), 80)]
        out = gif2c.render_cpp(frames, 2, 1, "x.gif")

        self.assertIn('#include "gif_anim.h"', out)
        self.assertIn("const uint16_t kWidth = 2;", out)
        self.assertIn("const uint16_t kHeight = 1;", out)
        self.assertIn("const size_t kFrameCount = 2;", out)
        self.assertIn("{kFrame0, 120}", out)
        self.assertIn("{kFrame1, 80}", out)
        self.assertIn("0x01, 0x02, 0x03, 0x04, 0x05, 0x06", out)

    def test_main_returns_error_on_bad_input(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = pathlib.Path(tmp) / "out.cpp"
            rc = gif2c.main([str(pathlib.Path(tmp) / "missing.gif"), str(out)])
            self.assertEqual(rc, 1)
            self.assertFalse(out.exists())


if __name__ == "__main__":
    unittest.main()
