import pathlib
import tempfile
import unittest

from PIL import Image

from pixelboard import gifproc
from pixelboard.gifproc import GifError, Layout, load, render

W1, H = 64, 32


def make_gif(path, frames, durations):
    frames[0].save(path, save_all=True, append_images=frames[1:], duration=durations, loop=0)


def px(rgb, width, x, y):
    i = (y * width + x) * 3
    return tuple(rgb[i:i + 3])


def is_red(p):
    return p[0] > 200 and p[1] < 60 and p[2] < 60


def is_blue(p):
    return p[2] > 200 and p[0] < 60


def pair_frame(left=True, right=True):
    """40x20 transparent: red 'left eye' x4..19, blue 'right eye' x20..35, rows 5..14."""
    img = Image.new("RGBA", (40, 20), (0, 0, 0, 0))
    if left:
        img.paste((255, 0, 0, 255), (4, 5, 20, 15))
    if right:
        img.paste((0, 0, 255, 255), (20, 5, 36, 15))
    return img


class GifTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = pathlib.Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def red_square_gif(self, size=20, frames=1, durations=None):
        path = self.dir / "red.gif"
        imgs = [Image.new("RGB", (size, size), (255 - i * 10, 0, 0)) for i in range(frames)]
        make_gif(path, imgs, durations or [100] * frames)
        return path


class LoadTest(GifTestCase):
    def test_scales_to_fit_and_centers_on_black(self):
        # 20x20 -> fit 1.6 -> 32x32 centred on 64x32: columns 16..47.
        src = load(self.red_square_gif(frames=2, durations=[150, 300]))
        frames = render(src, Layout())
        self.assertEqual([d for _, d in frames], [150, 300])
        for rgb, _ in frames:
            self.assertEqual(len(rgb), W1 * H * 3)
            self.assertTrue(is_red(px(rgb, W1, 20, 10)))
            self.assertTrue(is_red(px(rgb, W1, 44, 25)))
            self.assertEqual(px(rgb, W1, 2, 10), (0, 0, 0))
            self.assertEqual(px(rgb, W1, 60, 10), (0, 0, 0))

    def test_missing_duration_uses_default(self):
        path = self.dir / "still.gif"
        Image.new("RGB", (4, 4), (0, 255, 0)).save(path)
        frames = render(load(path), Layout())
        self.assertEqual(len(frames), 1)
        self.assertEqual(frames[0][1], gifproc.DEFAULT_DURATION_MS)

    def test_zero_duration_uses_default(self):
        src = load(self.red_square_gif(frames=2, durations=[0, 0]))
        self.assertEqual([d for _, d in render(src, Layout())], [100, 100])

    def test_duration_is_clamped(self):
        self.assertEqual(gifproc.clamp_duration(5), gifproc.MIN_DURATION_MS)
        self.assertEqual(gifproc.clamp_duration(999999), gifproc.MAX_DURATION_MS)
        self.assertEqual(gifproc.clamp_duration(None), gifproc.DEFAULT_DURATION_MS)
        self.assertEqual(gifproc.clamp_duration(0), gifproc.DEFAULT_DURATION_MS)
        self.assertEqual(gifproc.clamp_duration(200), 200)

    def test_missing_file_raises(self):
        with self.assertRaises(GifError) as cm:
            load(self.dir / "nope.gif")
        self.assertIn("找不到", cm.exception.user_message)

    def test_png_renamed_gif_rejected(self):
        path = self.dir / "image.gif"
        Image.new("RGB", (4, 4)).save(path, format="PNG")
        with self.assertRaises(GifError) as cm:
            load(path)
        self.assertIn("不是 GIF", cm.exception.user_message)

    def test_garbage_file_raises(self):
        path = self.dir / "bad.gif"
        path.write_bytes(b"not an image at all")
        with self.assertRaises(GifError) as cm:
            load(path)
        self.assertIn("不是 GIF", cm.exception.user_message)

    def test_too_many_frames_raises(self):
        path = self.dir / "long.gif"
        imgs = [Image.new("RGB", (2, 2), (i, i, i)) for i in range(gifproc.MAX_FRAMES + 1)]
        make_gif(path, imgs, [100] * len(imgs))
        with self.assertRaises(GifError) as cm:
            load(path)
        self.assertIn("64", cm.exception.user_message)

    def test_huge_dimensions_ok(self):
        path = self.dir / "huge.gif"
        Image.new("RGB", (4000, 4000), (255, 0, 0)).save(path)
        frames = render(load(path), Layout())
        self.assertEqual(len(frames[0][0]), W1 * H * 3)
        self.assertTrue(is_red(px(frames[0][0], W1, 32, 16)))


class LayoutTest(GifTestCase):
    def test_offset_moves_content_by_pixels(self):
        src = load(self.red_square_gif())
        base = render(src, Layout())[0][0]
        moved = render(src, Layout(offset_x=3))[0][0]
        for y in range(H):
            for x in range(W1 - 3):
                self.assertEqual(px(moved, W1, x + 3, y), px(base, W1, x, y), (x, y))

    def test_scale_half_shrinks_around_center(self):
        # fit 1.6 x 0.5 -> 16x16 centred: columns 24..39, rows 8..23.
        rgb = render(load(self.red_square_gif()), Layout(scale=0.5))[0][0]
        row = [is_red(px(rgb, W1, x, 16)) for x in range(W1)]
        self.assertEqual(row.index(True), 24)
        self.assertEqual(sum(row), 16)
        self.assertEqual(px(rgb, W1, 32, 4), (0, 0, 0))

    def test_layout_validation(self):
        src = load(self.red_square_gif())
        for bad in (Layout(chain=3), Layout(scale=0), Layout(scale=5), Layout(offset_x=999),
                    Layout(offset_y=-200), Layout(chain=1, split=True)):
            with self.assertRaises(GifError, msg=str(bad)):
                render(src, bad)


class SplitTest(GifTestCase):
    """split: crop to visible content, cut in two, one half per 64x32 panel."""

    def pair_gif(self, frames):
        path = self.dir / "pair.gif"
        make_gif(path, frames, [100] * len(frames))
        return load(path)

    def test_each_half_goes_to_its_own_panel(self):
        # Halves 16x10 -> fit min(64/16, 32/10)=3.2 -> 51x32 at x=6 in each panel.
        frames = render(self.pair_gif([pair_frame(), pair_frame().transpose(Image.FLIP_TOP_BOTTOM)]),
                        Layout(chain=2, split=True))
        rgb = frames[0][0]
        for y in (2, 16, 29):
            for x in (10, 30, 50):
                self.assertTrue(is_red(px(rgb, 128, x, y)), (x, y))
            for x in (74, 94, 114):
                self.assertTrue(is_blue(px(rgb, 128, x, y)), (x, y))
            for x in (0, 3, 60, 63, 64, 67, 124, 127):  # margins and the seam stay black
                self.assertEqual(px(rgb, 128, x, y), (0, 0, 0), (x, y))

    def test_crop_is_shared_by_all_frames(self):
        frames = render(self.pair_gif([pair_frame(right=False), pair_frame()]), Layout(chain=2, split=True))
        first = frames[0][0]
        self.assertTrue(is_red(px(first, 128, 30, 16)))
        for x in range(64, 128):
            self.assertEqual(px(first, 128, x, 16), (0, 0, 0), x)

    def test_split_offset_clipped_per_panel(self):
        rgb = render(self.pair_gif([pair_frame()]), Layout(chain=2, split=True, offset_x=60))[0][0]
        for y in range(H):
            for x in range(64, 128):
                self.assertFalse(is_red(px(rgb, 128, x, y)), (x, y))

    def test_fully_transparent_raises(self):
        src = self.pair_gif([Image.new("RGBA", (10, 10), (0, 0, 0, 0))])
        with self.assertRaises(GifError) as cm:
            render(src, Layout(chain=2, split=True))
        self.assertIn("透明", cm.exception.user_message)


if __name__ == "__main__":
    unittest.main()
