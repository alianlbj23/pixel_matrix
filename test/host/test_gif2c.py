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


def pair_frame(left=True, right=True):
    """40x20 transparent canvas: red 'left eye' x4..19, blue 'right eye' x20..35, rows 5..14."""
    img = Image.new("RGBA", (40, 20), (0, 0, 0, 0))
    if left:
        img.paste((255, 0, 0, 255), (4, 5, 20, 15))
    if right:
        img.paste((0, 0, 255, 255), (20, 5, 36, 15))
    return img


def px(rgb, width, x, y):
    i = (y * width + x) * 3
    return tuple(rgb[i:i + 3])


class SplitTest(unittest.TestCase):
    """--split N: crop to visible content, cut into N strips, one strip per panel."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = pathlib.Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_each_half_goes_to_its_own_panel(self):
        src = self.dir / "pair.gif"
        make_gif(src, [pair_frame(), pair_frame().transpose(Image.FLIP_TOP_BOTTOM)], [100, 100])

        # Two 8x4 panels. Halves are 16x10 -> scale 0.4 -> 6x4, centered at x=1 in each panel.
        frames = gif2c.load_frames(src, 16, 4, split=2)

        rgb = frames[0][0]
        for y in range(4):
            for x in (2, 3, 4, 5):
                r, g, b = px(rgb, 16, x, y)
                self.assertTrue(r > 200 and b < 60, (x, y, (r, g, b)))
            for x in (10, 11, 12, 13):
                r, g, b = px(rgb, 16, x, y)
                self.assertTrue(b > 200 and r < 60, (x, y, (r, g, b)))
            for x in (0, 7, 8, 15):  # outer margins and the seam stay black
                self.assertEqual(px(rgb, 16, x, y), (0, 0, 0), (x, y))

    def test_crop_is_shared_by_all_frames(self):
        # Frame 0 has only the left eye. With a per-frame crop it would be stretched over
        # both panels; with the shared crop it stays on the left panel.
        src = self.dir / "blink.gif"
        make_gif(src, [pair_frame(right=False), pair_frame()], [100, 100])

        frames = gif2c.load_frames(src, 16, 4, split=2)

        first = frames[0][0]
        for y in range(4):
            r, _, b = px(first, 16, 3, y)
            self.assertTrue(r > 200 and b < 60)
            for x in range(8, 16):
                self.assertEqual(px(first, 16, x, y), (0, 0, 0), (x, y))

    def test_split_must_divide_width(self):
        src = self.dir / "pair.gif"
        make_gif(src, [pair_frame()], [100])
        with self.assertRaises(gif2c.GifError):
            gif2c.load_frames(src, 15, 4, split=2)

    def test_split_out_of_range(self):
        src = self.dir / "pair.gif"
        make_gif(src, [pair_frame()], [100])
        for bad in (0, -1, gif2c.MAX_SPLIT + 1):
            with self.assertRaises(gif2c.GifError):
                gif2c.load_frames(src, 16, 4, split=bad)

    def test_fully_transparent_gif_raises(self):
        src = self.dir / "empty.gif"
        make_gif(src, [Image.new("RGBA", (10, 10), (0, 0, 0, 0))], [100])
        with self.assertRaises(gif2c.GifError):
            gif2c.load_frames(src, 16, 4, split=2)

    def test_main_accepts_split(self):
        src = self.dir / "pair.gif"
        make_gif(src, [pair_frame()], [100])
        out = self.dir / "out.cpp"
        rc = gif2c.main([str(src), str(out), "--width", "16", "--height", "4", "--split", "2"])
        self.assertEqual(rc, 0)
        self.assertIn("const uint16_t kWidth = 16;", out.read_text())


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
