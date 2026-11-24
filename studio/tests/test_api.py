import base64
import pathlib
import tempfile
import unittest

from PIL import Image

from pixelboard import pxa, pxg
from pixelboard.app import StudioApi
from pixelboard.board import BOARD_PID, BOARD_VID, BoardError, PortInfo
from pixelboard.flasher import MSG_PORT_BUSY

from tests import mp3_fixtures as fx

FIX = pathlib.Path(__file__).resolve().parent / "fixtures"
APP_HEAD = (FIX / "app_head.bin").read_bytes()
PART = (FIX / "partition-table.bin").read_bytes()
IMAGES = {"bootloader": b"B" * 16, "partition_table": PART, "app": APP_HEAD + b"A" * 64}
LAYOUT = {"chain": 2, "split": True, "offset_x": 0, "offset_y": 0, "scale": 1.0, "brightness": 90, "swap": False}


class FakeFlasher:
    """Board already running the bundled firmware (so plans are update_content)."""
    instances = []

    def __init__(self, port, progress=None, connect_error=None):
        self.port, self.progress, self.connect_error = port, progress, connect_error
        self.writes, self.closed, self.reset = [], False, False
        FakeFlasher.instances.append(self)

    def connect(self):
        if self.connect_error:
            raise self.connect_error

    def read(self, offset, size):
        return {0x10000: APP_HEAD, 0x8000: PART}[offset][:size]

    def write(self, regions):
        self.writes.extend(regions)
        if self.progress:
            self.progress("write", 1.0)

    def hard_reset(self):
        self.reset = True

    def close(self):
        self.closed = True


def board_ports():
    return [PortInfo("COM11", BOARD_VID, BOARD_PID, "")]


def pair_gif(path):
    frames = []
    for shift in (0, 2):
        img = Image.new("RGBA", (40, 20), (0, 0, 0, 0))
        img.paste((255, 0, 0, 255), (4 + shift, 5, 20, 15))
        img.paste((0, 0, 255, 255), (20, 5, 36 - shift, 15))
        frames.append(img)
    frames[0].save(path, save_all=True, append_images=frames[1:], duration=[120, 80], loop=0)


class ApiTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = pathlib.Path(self._tmp.name)
        FakeFlasher.instances.clear()
        self.api = StudioApi(list_ports=board_ports, flasher_factory=FakeFlasher, images_loader=lambda: IMAGES)
        self.gif = self.dir / "eyes.gif"
        pair_gif(self.gif)

    def tearDown(self):
        self._tmp.cleanup()

    def write(self, name, data):
        path = self.dir / name
        path.write_bytes(data)
        return str(path)


class GifApiTest(ApiTestCase):
    def test_open_gif_then_preview_size(self):
        res = self.api.open_gif(str(self.gif))
        self.assertTrue(res["ok"], res)
        self.assertEqual((res["name"], res["frames"]), ("eyes.gif", 2))
        prev = self.api.render_preview(LAYOUT)
        self.assertTrue(prev["ok"], prev)
        self.assertEqual((prev["width"], prev["height"], prev["brightness"]), (128, 32, 90))
        self.assertEqual([f["ms"] for f in prev["frames"]], [120, 80])
        self.assertEqual(len(base64.b64decode(prev["frames"][0]["rgb_b64"])), 128 * 32 * 3)

    def test_preview_without_gif(self):
        res = self.api.render_preview(LAYOUT)
        self.assertFalse(res["ok"])
        self.assertIn("GIF", res["message"])

    def test_bad_gif_message(self):
        res = self.api.open_gif(self.write("x.gif", b"junk"))
        self.assertFalse(res["ok"])
        self.assertIn("不是 GIF", res["message"])

    def test_bad_layout_message(self):
        self.api.open_gif(str(self.gif))
        for bad in ({**LAYOUT, "chain": 3}, {**LAYOUT, "scale": "big"}, {**LAYOUT, "brightness": 200}, {}):
            res = self.api.render_preview(bad)
            self.assertFalse(res["ok"], bad)
            self.assertTrue(res["message"])


class MusicApiTest(ApiTestCase):
    def test_add_tracks_rejects_bad_with_reason(self):
        res = self.api.add_tracks([self.write("good.mp3", fx.stereo_44k()), self.write("bad.mp3", b"nope" * 50)])
        self.assertTrue(res["ok"])
        self.assertEqual([t["name"] for t in res["tracks"]], ["good.mp3"])
        self.assertEqual(res["tracks"][0]["rate"], 44100)
        self.assertEqual(res["rejected"][0]["name"], "bad.mp3")
        self.assertIn("MP3", res["rejected"][0]["message"])

    def test_capacity_reported(self):
        a, b = fx.stereo_44k(10), fx.mono_44k(7)
        res = self.api.add_tracks([self.write("a.mp3", a), self.write("b.mp3", b)])
        self.assertEqual(res["used"], pxa.packed_size([len(a), len(b)]))
        self.assertEqual(res["capacity"], pxa.CAPACITY)

    def test_over_capacity_rejected(self):
        big = fx.stereo_44k(pxa.CAPACITY // 417 // 2 + 10)  # a bit over half the capacity each
        res = self.api.add_tracks([self.write("a.mp3", big), self.write("b.mp3", big)])
        self.assertEqual(len(res["tracks"]), 1)
        self.assertIn("容量", res["rejected"][0]["message"])

    def test_move_and_remove(self):
        self.api.add_tracks([self.write(n, fx.stereo_44k()) for n in ("a.mp3", "b.mp3", "c.mp3")])
        res = self.api.move_track(2, 0)
        self.assertEqual([t["name"] for t in res["tracks"]], ["c.mp3", "a.mp3", "b.mp3"])
        res = self.api.remove_track(1)
        self.assertEqual([t["name"] for t in res["tracks"]], ["c.mp3", "b.mp3"])
        self.assertFalse(self.api.remove_track(9)["ok"])
        self.assertFalse(self.api.move_track(0, 9)["ok"])

    def test_track_data_url(self):
        self.api.add_tracks([self.write("a.mp3", fx.stereo_44k(3))])
        res = self.api.track_data_url(0)
        self.assertTrue(res["url"].startswith("data:audio/mpeg;base64,"))
        self.assertFalse(self.api.track_data_url(5)["ok"])


class FlashApiTest(ApiTestCase):
    def test_preview_bytes_equal_flash_bytes(self):
        self.api.open_gif(str(self.gif))
        prev = self.api.render_preview(LAYOUT)
        plan = self.api.prepare_flash(LAYOUT, None)
        self.assertTrue(plan["ok"], plan)
        self.assertEqual(plan["kind"], "update_content")
        done = self.api.confirm_flash()
        self.assertTrue(done["ok"], done)
        flasher = FakeFlasher.instances[-1]
        written = dict(flasher.writes)
        anim = pxg.unpack(written[0x210000])
        self.assertEqual([rgb for rgb, _ in anim.frames], [base64.b64decode(f["rgb_b64"]) for f in prev["frames"]])
        self.assertEqual((anim.chain, anim.brightness, anim.swap, anim.split), (2, 90, False, True))
        self.assertNotIn(0x510000, written)  # no music added -> keep the board's music
        self.assertTrue(flasher.reset and flasher.closed)

    def test_music_written_when_added(self):
        self.api.open_gif(str(self.gif))
        self.api.add_tracks([self.write("a.mp3", fx.stereo_44k(3))])
        self.api.prepare_flash(LAYOUT, "COM11")
        self.api.confirm_flash()
        written = dict(FakeFlasher.instances[-1].writes)
        self.assertEqual([t.name for t in pxa.unpack(written[0x510000])], ["a.mp3"])

    def test_flash_without_board_message(self):
        api = StudioApi(list_ports=lambda: [], flasher_factory=FakeFlasher, images_loader=lambda: IMAGES)
        api.open_gif(str(self.gif))
        res = api.prepare_flash(LAYOUT, None)
        self.assertFalse(res["ok"])
        self.assertIn("找不到板子", res["message"])

    def test_flash_port_busy_message(self):
        busy = lambda port, progress=None: FakeFlasher(port, progress, BoardError(MSG_PORT_BUSY))  # noqa: E731
        api = StudioApi(list_ports=board_ports, flasher_factory=busy, images_loader=lambda: IMAGES)
        api.open_gif(str(self.gif))
        res = api.prepare_flash(LAYOUT, None)
        self.assertEqual(res, {"ok": False, "message": MSG_PORT_BUSY})
        self.assertTrue(FakeFlasher.instances[-1].closed)

    def test_cancel_closes_connection(self):
        self.api.open_gif(str(self.gif))
        self.api.prepare_flash(LAYOUT, None)
        self.assertTrue(self.api.cancel_flash()["ok"])
        self.assertTrue(FakeFlasher.instances[-1].closed)
        self.assertFalse(self.api.confirm_flash()["ok"])

    def test_nothing_to_flash(self):
        res = self.api.prepare_flash(LAYOUT, None)  # no GIF, no music, firmware up to date
        self.assertFalse(res["ok"])
        self.assertIn("沒有要燒錄的內容", res["message"])


if __name__ == "__main__":
    unittest.main()
