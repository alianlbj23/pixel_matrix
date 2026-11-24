import base64
import hashlib
import json
import pathlib
import tempfile
import threading
import time
import unittest
from unittest import mock

from PIL import Image

from pixelboard import app as app_module
from pixelboard import layout, pxa, pxg, serialcmd, updater
from pixelboard.app import MSG_LOCKED, MSG_STOPPED, MSG_UPDATE_WHILE_FLASHING, MSG_UPDATING, StudioApi
from pixelboard.board import AUDIO_OFFSET, BOARD_PID, BOARD_VID, GIF_OFFSET, BoardError, PortInfo
from pixelboard.flasher import MSG_PORT_BUSY, Flasher

from tests import mp3_fixtures as fx

FIX = pathlib.Path(__file__).resolve().parent / "fixtures"
APP_HEAD = (FIX / "app_head.bin").read_bytes()
PART = (FIX / "partition-table.bin").read_bytes()
IMAGES = {"bootloader": b"B" * 16, "partition_table": PART, "app": APP_HEAD + b"A" * 64}
PLACEMENT = {"mode": "whole", "offset_x": 0, "offset_y": 0, "scale": 1.0, "brightness": 90}


class FakeFlasher(Flasher):
    """Board already running the bundled firmware (plans are update_content) unless blank=True.

    Uses the real Flasher.write_chunked; only the esptool calls are replaced.
    """
    instances = []
    on_piece = None  # optional callback(offset) run after each written piece

    def __init__(self, port, progress=None, connect_error=None, blank=False, app_md5=None, table=PART):
        super().__init__(port, progress)
        self.connect_error, self.blank = connect_error, blank
        self.app_md5 = app_md5  # None: the board's app equals the bundled one
        self.table = table
        self.pieces, self.closed, self.reset = [], False, False
        FakeFlasher.instances.append(self)

    def connect(self):
        if self.connect_error:
            raise self.connect_error
        self._esp = object()

    def read(self, offset, size):
        if self.blank:
            return b"\xff" * size
        return {0x10000: APP_HEAD, 0x8000: self.table}[offset][:size]

    def flash_md5(self, offset, size):
        assert offset == 0x10000, offset
        return self.app_md5 or hashlib.md5(IMAGES["app"][:size]).hexdigest()

    def _write_piece(self, offset, data):
        self.pieces.append((offset, bytes(data)))
        if FakeFlasher.on_piece:
            FakeFlasher.on_piece(offset)

    def hard_reset(self):
        self.reset = True

    def close(self, reset=False):
        self.closed = True
        self.closed_with_reset = reset
        self._esp = None


def blank_flasher(port, progress=None):
    return FakeFlasher(port, progress, blank=True)


def written(flasher) -> dict:
    """Re-joins the written pieces into {region offset: bytes}."""
    regions = {}
    start = None
    for offset, data in flasher.pieces:
        if start is not None and offset == start + len(regions[start]):
            regions[start] += data
        else:
            start = offset
            regions[start] = data
    return regions


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


class FakeClock:
    def __init__(self):
        self.now = 100.0

    def __call__(self):
        return self.now


class FakeWindow:
    def __init__(self, error=None, on_destroy=None):
        self.js = []
        self.error = error
        self.on_destroy = on_destroy  # pywebview runs the closing handlers inside destroy()
        self.destroyed = None

    def destroy(self):
        self.destroyed = self.on_destroy() if self.on_destroy else True

    def evaluate_js(self, code):
        self.js.append(code)
        if self.error:
            raise self.error

    def wait_js(self, count=1, timeout=2.0):
        deadline = time.monotonic() + timeout
        while len(self.js) < count and time.monotonic() < deadline:
            time.sleep(0.005)
        return self.js


class ApiTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = pathlib.Path(self._tmp.name)
        self.layout_path = self.dir / "cfg" / "layout.json"
        FakeFlasher.instances.clear()
        FakeFlasher.on_piece = None
        self.identify_calls = []
        self.identify_error = None
        self.clock = FakeClock()
        self.api = self.make_api()
        self.gif = self.dir / "eyes.gif"
        pair_gif(self.gif)

    def tearDown(self):
        FakeFlasher.on_piece = None
        self._tmp.cleanup()

    def fake_identify(self, port, n):
        self.identify_calls.append((port, n))
        if self.identify_error:
            raise self.identify_error
        return f"已在 {n} 塊面板上顯示編號"

    def make_api(self, **kwargs):
        args = dict(list_ports=board_ports, flasher_factory=FakeFlasher, images_loader=lambda: IMAGES,
                    layout_path=self.layout_path, identify_fn=self.fake_identify, clock=self.clock)
        args.update(kwargs)
        return StudioApi(**args)

    def write(self, name, data):
        path = self.dir / name
        path.write_bytes(data)
        return str(path)


class GifApiTest(ApiTestCase):
    def test_open_gif_then_preview_size(self):
        res = self.api.open_gif(str(self.gif))
        self.assertTrue(res["ok"], res)
        self.assertEqual((res["name"], res["frames"]), ("eyes.gif", 2))
        self.assertTrue(self.api.set_panel_count(2)["ok"])
        prev = self.api.render_preview(PLACEMENT)
        self.assertTrue(prev["ok"], prev)
        self.assertEqual((prev["width"], prev["height"], prev["brightness"]), (128, 32, 90))
        self.assertEqual(prev["max_frames"], pxg.max_frames(2))
        self.assertEqual(prev["panels"], [{"index": 0, "x": 0, "y": 0, "w": 64, "h": 32, "rotation": 0},
                                          {"index": 1, "x": 64, "y": 0, "w": 64, "h": 32, "rotation": 0}])
        self.assertEqual([f["ms"] for f in prev["frames"]], [120, 80])
        self.assertEqual(len(base64.b64decode(prev["frames"][0]["rgb_b64"])), 128 * 32 * 3)

    def test_preview_without_gif(self):
        res = self.api.render_preview(PLACEMENT)
        self.assertFalse(res["ok"])
        self.assertIn("GIF", res["message"])

    def test_bad_gif_message(self):
        res = self.api.open_gif(self.write("x.gif", b"junk"))
        self.assertFalse(res["ok"])
        self.assertIn("不是 GIF", res["message"])

    def test_bad_placement_message(self):
        self.api.open_gif(str(self.gif))
        for bad in ({**PLACEMENT, "mode": "tiles"}, {**PLACEMENT, "scale": "big"}, {**PLACEMENT, "brightness": 200},
                    {**PLACEMENT, "offset_x": 1.5}, {**PLACEMENT, "offset_y": True}, {**PLACEMENT, "scale": True},
                    {}, None, "whole"):
            res = self.api.render_preview(bad)
            self.assertFalse(res["ok"], bad)
            self.assertTrue(res["message"], bad)

    def test_split_needs_grid(self):
        self.api.open_gif(str(self.gif))
        self.api.apply_preset("column")
        self.api.set_panel_count(2)
        self.assertTrue(self.api.rotate_panel(1)["ok"])
        self.assertFalse(self.api.get_arrangement()["grid"])
        res = self.api.render_preview({**PLACEMENT, "mode": "split"})
        self.assertFalse(res["ok"])
        self.assertIn("整張", res["message"])

    def test_frame_limit_message(self):
        # max_frames(8) is 63 with the 3 MB gif partition, so a 64-frame GIF is one too many.
        frames = [Image.new("RGB", (16, 8), (i * 3, 255 - i * 3, i)) for i in range(64)]
        path = self.dir / "long.gif"
        frames[0].save(path, save_all=True, append_images=frames[1:], duration=50, loop=0)
        self.assertEqual(self.api.open_gif(str(path))["frames"], 64)
        self.assertTrue(self.api.set_panel_count(8)["ok"])
        res = self.api.render_preview(PLACEMENT)
        self.assertFalse(res["ok"])
        self.assertEqual(res["message"], f"目前 8 塊面板最多 {pxg.max_frames(8)} 格，這個 GIF 有 64 格")
        self.assertIn("最多", self.api.prepare_flash(PLACEMENT, None)["message"])

    def test_preview_blank_between_panels(self):
        self.api.open_gif(str(self.gif))
        self.api.set_panel_count(2)
        res = self.api.move_panel(1, 80, 0)  # 16 px gap, too far to snap
        self.assertEqual(res["arrangement"]["panels"][1]["x"], 80, res)
        prev = self.api.render_preview({**PLACEMENT, "scale": 4.0})
        self.assertEqual(prev["width"], 144)
        rgb = base64.b64decode(prev["frames"][0]["rgb_b64"])
        gap = [rgb[(y * 144 + x) * 3:(y * 144 + x) * 3 + 3] for y in range(32) for x in range(64, 80)]
        self.assertTrue(all(px == b"\0\0\0" for px in gap))
        self.assertTrue(any(rgb[(y * 144 + x) * 3:(y * 144 + x) * 3 + 3] != b"\0\0\0"
                            for y in range(32) for x in range(64)))


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
        self.assertFalse(self.api.remove_track(True)["ok"])

    def test_track_data_url(self):
        self.api.add_tracks([self.write("a.mp3", fx.stereo_44k(3))])
        res = self.api.track_data_url(0)
        self.assertTrue(res["url"].startswith("data:audio/mpeg;base64,"))
        self.assertFalse(self.api.track_data_url(5)["ok"])


class ArrangementApiTest(ApiTestCase):
    def test_default_is_one_panel(self):
        res = self.api.get_arrangement()
        self.assertEqual(res, {"ok": True, "arrangement": layout.to_dict(layout.preset("row", 1)), "grid": True,
                               "max_frames": pxg.max_frames(1), "power_warning": False})

    def test_arrangement_api_persists(self):
        res = self.api.set_panel_count(3)
        self.assertTrue(res["ok"], res)
        self.assertEqual(len(res["arrangement"]["panels"]), 3)
        self.assertTrue(res["power_warning"])
        self.assertEqual(res["max_frames"], pxg.max_frames(3))
        self.assertEqual(layout.load(self.layout_path), layout.preset("row", 3))
        res = self.api.rotate_panel(2)
        self.assertEqual(res["arrangement"]["panels"][2]["rotation"], 90)
        self.assertEqual(json.loads(self.layout_path.read_text())["panels"][2]["rotation"], 90)
        self.assertTrue(self.api.set_panel_count(4)["ok"])
        res = self.api.apply_preset("snake")
        self.assertTrue(res["ok"], res)
        self.assertFalse(res["grid"])  # mixed rotations
        self.assertEqual(layout.load(self.layout_path), layout.preset("snake", 4))
        again = self.make_api()
        self.assertEqual(again.get_arrangement()["arrangement"], res["arrangement"])

    def test_move_panel_snaps(self):
        self.api.set_panel_count(2)
        res = self.api.move_panel(1, 5, 37)  # within 8 px of "below panel 0, left edges aligned"
        self.assertTrue(res["ok"], res)
        self.assertEqual(res["arrangement"]["panels"][1], {"index": 1, "x": 0, "y": 32, "rotation": 0})
        self.assertTrue(res["grid"])
        self.assertEqual(layout.load(self.layout_path).panels[1], layout.Panel(1, 0, 32, 0))

    def test_move_panel_overlap_reverts(self):
        self.api.set_panel_count(2)
        before = self.api.get_arrangement()["arrangement"]
        res = self.api.move_panel(1, 20, 10)
        self.assertFalse(res["ok"])
        self.assertIn("重疊", res["message"])
        self.assertEqual(res["arrangement"], before)
        self.assertEqual(self.api.get_arrangement()["arrangement"], before)

    def test_move_panel_normalizes(self):
        self.api.set_panel_count(2)
        res = self.api.move_panel(1, -64, 0)  # to the left of panel 0
        self.assertTrue(res["ok"], res)
        self.assertEqual([(p["x"], p["y"]) for p in res["arrangement"]["panels"]], [(64, 0), (0, 0)])

    def test_rotate_overlap_keeps_arrangement(self):
        self.api.set_panel_count(2)
        self.api.apply_preset("column")
        before = self.api.get_arrangement()["arrangement"]
        res = self.api.rotate_panel(0)  # 32x64 at (0,0) would cover panel 1 at (0,32)
        self.assertFalse(res["ok"])
        self.assertIn("重疊", res["message"])
        self.assertEqual(res["arrangement"], before)

    def test_rotate_cycles(self):
        for expected in (90, 180, 270, 0):
            res = self.api.rotate_panel(0)
            self.assertEqual(res["arrangement"]["panels"][0]["rotation"], expected)

    def test_bad_arrangement_args(self):
        self.api.set_panel_count(2)
        before = self.api.get_arrangement()["arrangement"]
        calls = [lambda: self.api.set_panel_count(0), lambda: self.api.set_panel_count(9),
                 lambda: self.api.set_panel_count(True), lambda: self.api.set_panel_count("2"),
                 lambda: self.api.set_panel_count(2.5), lambda: self.api.set_panel_count(None),
                 lambda: self.api.apply_preset("nope"), lambda: self.api.apply_preset(3),
                 lambda: self.api.move_panel("a", 0, 0), lambda: self.api.move_panel(5, 0, 0),
                 lambda: self.api.move_panel(1, 1.5, 0), lambda: self.api.move_panel(1, True, 0),
                 lambda: self.api.move_panel(1, 0, float("nan")), lambda: self.api.move_panel(1, 99999, 0),
                 lambda: self.api.rotate_panel(9), lambda: self.api.rotate_panel(-1),
                 lambda: self.api.rotate_panel(False)]
        for i, call in enumerate(calls):
            res = call()
            self.assertFalse(res["ok"], i)
            self.assertTrue(res["message"], i)
        self.assertEqual(self.api.get_arrangement()["arrangement"], before)

    def test_integral_float_accepted(self):
        self.assertTrue(self.api.set_panel_count(2.0)["ok"])
        res = self.api.move_panel(1.0, 0.0, 32.0)
        self.assertTrue(res["ok"], res)
        self.assertEqual(res["arrangement"]["panels"][1], {"index": 1, "x": 0, "y": 32, "rotation": 0})

    def test_canvas_size_capped(self):
        self.api.set_panel_count(2)
        res = self.api.move_panel(1, 1000, 0)
        self.assertFalse(res["ok"])
        self.assertIn("1024", res["message"])
        self.assertEqual(len(self.api.get_arrangement()["arrangement"]["panels"]), 2)
        self.assertEqual(self.api.get_arrangement()["arrangement"]["panels"][1]["x"], 64)

    def test_huge_or_corrupt_layout_file_falls_back(self):
        self.layout_path.parent.mkdir(parents=True)
        huge = {"version": 1, "panels": [{"index": 0, "x": 0, "y": 0, "rotation": 0},
                                         {"index": 1, "x": 3000, "y": 3000, "rotation": 0}]}
        for content in (json.dumps(huge), "{not json", json.dumps({"panels": [{"index": 0, "x": 0.5}]})):
            self.layout_path.write_text(content)
            with self.assertLogs("pixelboard", level="WARNING"):
                api = self.make_api()
            self.assertEqual(api.get_arrangement()["arrangement"], layout.to_dict(layout.preset("row", 1)))

    def test_save_failure_reported(self):
        self.layout_path.parent.mkdir(parents=True)
        self.layout_path.mkdir()  # a directory where the file should be: open() fails
        with self.assertLogs("pixelboard", level="ERROR"):
            res = self.api.set_panel_count(2)
        self.assertTrue(res["ok"])
        self.assertIn("無法儲存", res["message"])
        self.assertEqual(len(res["arrangement"]["panels"]), 2)


class IdentifyApiTest(ApiTestCase):
    def test_identify_uses_panel_count(self):
        self.api.set_panel_count(3)
        res = self.api.identify("COM7")
        self.assertEqual(res, {"ok": True, "message": "已在 3 塊面板上顯示編號"})
        self.assertEqual(self.identify_calls, [("COM7", 3)])

    def test_identify_finds_board(self):
        self.assertTrue(self.api.identify(None)["ok"])
        self.assertEqual(self.identify_calls, [("COM11", 1)])
        api = self.make_api(list_ports=lambda: [])
        res = api.identify(None)
        self.assertFalse(res["ok"])
        self.assertIn("找不到板子", res["message"])

    def test_identify_while_showing(self):
        self.assertTrue(self.api.identify("COM11")["ok"])
        self.clock.now += 3
        res = self.api.identify("COM11")
        self.assertFalse(res["ok"])
        self.assertEqual(res["message"], "辨識畫面還在顯示中，請稍候")
        self.assertEqual(len(self.identify_calls), 1)
        self.clock.now += 3
        self.assertTrue(self.api.identify("COM11")["ok"])
        self.assertEqual(len(self.identify_calls), 2)

    def test_identify_failure_message(self):
        self.identify_error = BoardError("板子回報錯誤：panel init failed")
        res = self.api.identify("COM11")
        self.assertEqual(res, {"ok": False, "message": "板子回報錯誤：panel init failed"})
        self.identify_error = None
        self.assertTrue(self.api.identify("COM11")["ok"])  # a failure does not start the 5 s wait

    def test_identify_needs_firmware_flag(self):
        self.identify_error = serialcmd.NeedsFirmwareError()
        res = self.api.identify("COM11")
        self.assertEqual(res, {"ok": False, "needs_firmware": True,
                               "message": "這塊板子還沒有新版韌體，請先按『安裝/更新韌體』"})

    def test_identify_bad_port(self):
        res = self.api.identify(5)
        self.assertFalse(res["ok"])
        self.assertEqual(self.identify_calls, [])


class FlashApiTest(ApiTestCase):
    def test_preview_equals_flash_inverse(self):
        self.api.set_panel_count(4)
        self.api.apply_preset("snake")
        arr = layout.preset("snake", 4)
        self.api.open_gif(str(self.gif))
        placement = {**PLACEMENT, "offset_x": 3, "offset_y": -2, "scale": 1.5}  # snake is not a grid: whole
        prev = self.api.render_preview(placement)
        self.assertTrue(prev["ok"], prev)
        self.assertEqual((prev["width"], prev["height"]), (128, 64))
        plan = self.api.prepare_flash(placement, None)
        self.assertTrue(plan["ok"], plan)
        self.assertEqual(plan["kind"], "update_content")
        done = self.api.confirm_flash()
        self.assertTrue(done["ok"], done)
        flasher = FakeFlasher.instances[-1]
        regions = written(flasher)
        anim = pxg.unpack(regions[GIF_OFFSET])
        self.assertEqual((anim.chain, anim.brightness, anim.split), (4, 90, False))
        self.assertEqual(anim.panels, [(p.x, p.y, p.rotation) for p in arr.panels])
        self.assertEqual([ms for _, ms in anim.frames], [f["ms"] for f in prev["frames"]])
        for (chain, _), shown in zip(anim.frames, prev["frames"]):
            canvas, cw, ch = layout.canvas_frame(chain, arr)
            self.assertEqual((cw, ch), (128, 64))
            self.assertEqual(canvas, base64.b64decode(shown["rgb_b64"]))
        self.assertNotIn(AUDIO_OFFSET, regions)  # no music added -> keep the board's music
        self.assertTrue(flasher.reset and flasher.closed)

    def test_preview_equals_flash_split_with_gap(self):
        self.api.set_panel_count(2)
        self.api.move_panel(1, 90, 0)  # two separately mounted "eyes"
        arr = layout.Arrangement((layout.Panel(0, 0, 0, 0), layout.Panel(1, 90, 0, 0)))
        self.api.open_gif(str(self.gif))
        placement = {**PLACEMENT, "mode": "split"}
        prev = self.api.render_preview(placement)
        self.assertTrue(prev["ok"], prev)
        self.assertTrue(self.api.prepare_flash(placement, None)["ok"])
        self.assertTrue(self.api.confirm_flash()["ok"])
        anim = pxg.unpack(written(FakeFlasher.instances[-1])[GIF_OFFSET])
        self.assertTrue(anim.split)
        for (chain, _), shown in zip(anim.frames, prev["frames"]):
            self.assertEqual(layout.canvas_frame(chain, arr)[0], base64.b64decode(shown["rgb_b64"]))

    def test_music_written_when_added(self):
        self.api.open_gif(str(self.gif))
        self.api.add_tracks([self.write("a.mp3", fx.stereo_44k(3))])
        self.assertTrue(self.api.prepare_flash(PLACEMENT, "COM11")["ok"])
        self.assertTrue(self.api.confirm_flash()["ok"])
        regions = written(FakeFlasher.instances[-1])
        self.assertEqual([t.name for t in pxa.unpack(regions[AUDIO_OFFSET])], ["a.mp3"])

    def test_flash_without_board_message(self):
        api = self.make_api(list_ports=lambda: [])
        api.open_gif(str(self.gif))
        res = api.prepare_flash(PLACEMENT, None)
        self.assertFalse(res["ok"])
        self.assertIn("找不到板子", res["message"])
        self.assertTrue(api.render_preview(PLACEMENT)["ok"])  # a failed prepare does not lock editing

    def test_flash_port_busy_message(self):
        busy = lambda port, progress=None: FakeFlasher(port, progress, BoardError(MSG_PORT_BUSY))  # noqa: E731
        api = self.make_api(flasher_factory=busy)
        api.open_gif(str(self.gif))
        res = api.prepare_flash(PLACEMENT, None)
        self.assertEqual(res, {"ok": False, "message": MSG_PORT_BUSY})
        self.assertTrue(FakeFlasher.instances[-1].closed)

    def test_cancel_closes_connection(self):
        self.api.open_gif(str(self.gif))
        self.assertTrue(self.api.prepare_flash(PLACEMENT, None)["ok"])
        self.assertTrue(self.api.cancel_flash()["ok"])
        self.assertTrue(FakeFlasher.instances[-1].closed)
        self.assertTrue(FakeFlasher.instances[-1].closed_with_reset)  # not left in download mode
        self.assertFalse(self.api.confirm_flash()["ok"])

    def test_write_failure_resets_board(self):
        self.api.open_gif(str(self.gif))
        self.assertTrue(self.api.prepare_flash(PLACEMENT, None)["ok"])
        fl = FakeFlasher.instances[-1]

        def boom(offset, data):
            raise BoardError("寫入失敗")
        fl._write_piece = boom
        res = self.api.confirm_flash()
        self.assertEqual(res, {"ok": False, "message": "寫入失敗"})
        self.assertTrue(fl.closed_with_reset)
        self.assertFalse(fl.reset)  # the failed confirm did not reach its own hard_reset

    def test_successful_confirm_does_not_reset_twice(self):
        self.api.open_gif(str(self.gif))
        self.assertTrue(self.api.prepare_flash(PLACEMENT, None)["ok"])
        self.assertTrue(self.api.confirm_flash()["ok"])
        fl = FakeFlasher.instances[-1]
        self.assertTrue(fl.reset)
        self.assertFalse(fl.closed_with_reset)

    def test_nothing_to_flash(self):
        res = self.api.prepare_flash(PLACEMENT, None)  # no GIF, no music, firmware up to date
        self.assertFalse(res["ok"])
        self.assertIn("沒有要燒錄的內容", res["message"])

    def test_firmware_only_plan(self):
        api = self.make_api(flasher_factory=blank_flasher)
        api.open_gif(str(self.gif))
        api.add_tracks([self.write("a.mp3", fx.stereo_44k(3))])
        plan = api.prepare_flash(None, None, firmware_only=True)  # placement is not needed
        self.assertTrue(plan["ok"], plan)
        self.assertEqual(plan["kind"], "first_install")
        self.assertEqual(plan["bytes"], sum(len(d) for d in IMAGES.values()))
        self.assertTrue(api.confirm_flash()["ok"])
        self.assertEqual(sorted(written(FakeFlasher.instances[-1])), [0x0, 0x8000, 0x10000])

    def test_firmware_only_up_to_date(self):
        self.api.open_gif(str(self.gif))
        res = self.api.prepare_flash(PLACEMENT, None, firmware_only=True)
        self.assertFalse(res["ok"])
        self.assertTrue(res["up_to_date"])
        self.assertIn("最新", res["message"])
        self.assertTrue(FakeFlasher.instances[-1].closed)
        self.assertTrue(FakeFlasher.instances[-1].closed_with_reset)
        self.assertTrue(self.api.render_preview(PLACEMENT)["ok"])

    def test_firmware_only_rewrites_corrupt_app(self):
        def corrupt(port, progress=None):
            return FakeFlasher(port, progress, app_md5="0" * 32)
        api = self.make_api(flasher_factory=corrupt)
        plan = api.prepare_flash(None, None, firmware_only=True)
        self.assertTrue(plan["ok"], plan)
        self.assertEqual(plan["kind"], "update_firmware")
        self.assertEqual(plan["bytes"], len(IMAGES["app"]))
        self.assertTrue(api.confirm_flash()["ok"])
        self.assertEqual(written(FakeFlasher.instances[-1]), {0x10000: IMAGES["app"]})

    def test_firmware_only_old_table_flags_music(self):
        old_table = PART[:40] + bytes([PART[40] ^ 0xFF]) + PART[41:]
        api = self.make_api(flasher_factory=lambda port, progress=None: FakeFlasher(port, progress, table=old_table))
        plan = api.prepare_flash(None, None, firmware_only=True)
        self.assertTrue(plan["ok"], plan)
        self.assertEqual(plan["kind"], "update_firmware")
        self.assertIs(plan["music_may_be_lost"], True)
        self.assertTrue(api.cancel_flash()["ok"])

    def test_plan_without_music_risk(self):
        api = self.make_api(flasher_factory=lambda port, progress=None: FakeFlasher(port, progress, app_md5="0" * 32))
        plan = api.prepare_flash(None, None, firmware_only=True)
        self.assertIs(plan["music_may_be_lost"], False)
        self.assertTrue(api.cancel_flash()["ok"])

    def test_firmware_only_must_be_bool(self):
        res = self.api.prepare_flash(PLACEMENT, None, firmware_only="yes")
        self.assertFalse(res["ok"])
        self.assertEqual(FakeFlasher.instances, [])

    def test_edits_rejected_while_flashing(self):
        self.api.open_gif(str(self.gif))
        mp3 = self.write("a.mp3", fx.stereo_44k(3))
        self.api.add_tracks([mp3, mp3])
        self.assertTrue(self.api.prepare_flash(PLACEMENT, None)["ok"])
        edits = {
            "open_gif": lambda: self.api.open_gif(str(self.gif)),
            "add_tracks": lambda: self.api.add_tracks([mp3]),
            "remove_track": lambda: self.api.remove_track(0),
            "move_track": lambda: self.api.move_track(0, 1),
            "set_panel_count": lambda: self.api.set_panel_count(2),
            "apply_preset": lambda: self.api.apply_preset("column"),
            "move_panel": lambda: self.api.move_panel(0, 10, 10),
            "rotate_panel": lambda: self.api.rotate_panel(0),
            "render_preview": lambda: self.api.render_preview(PLACEMENT),
            "identify": lambda: self.api.identify("COM11"),
        }
        for name, call in edits.items():
            self.assertEqual(call(), {"ok": False, "message": MSG_LOCKED}, name)
        self.assertEqual(MSG_LOCKED, "燒錄中，請先停止燒錄")
        self.assertEqual(self.identify_calls, [])
        self.assertEqual(self.api.find_board()["ports"], ["COM11"])
        self.assertTrue(self.api.get_arrangement()["ok"])
        self.assertTrue(self.api.track_data_url(0)["ok"])
        self.assertTrue(self.api.cancel_flash()["ok"])
        for name, call in edits.items():
            self.assertNotEqual(call().get("message"), MSG_LOCKED, name)
        self.clock.now += 10
        self.assertTrue(self.api.prepare_flash(PLACEMENT, None)["ok"])
        self.assertEqual(self.api.render_preview(PLACEMENT)["message"], MSG_LOCKED)
        self.assertTrue(self.api.confirm_flash()["ok"])
        self.assertTrue(self.api.render_preview(PLACEMENT)["ok"])

    def test_stop_flash_during_content(self):
        self.api.open_gif(str(self.gif))
        self.api.add_tracks([self.write("a.mp3", fx.stereo_44k(3))])
        self.assertTrue(self.api.prepare_flash(PLACEMENT, None)["ok"])
        FakeFlasher.on_piece = lambda offset: offset == GIF_OFFSET and self.api.stop_flash()
        res = self.api.confirm_flash()
        self.assertEqual(res, {"ok": False, "stopped": True, "message": MSG_STOPPED})
        self.assertEqual(MSG_STOPPED, "已停止燒錄：板子上的 GIF/音樂可能不完整，請重新燒錄")
        flasher = FakeFlasher.instances[-1]
        self.assertEqual(sorted(written(flasher)), [GIF_OFFSET])  # audio never started
        self.assertTrue(flasher.reset and flasher.closed)
        self.assertTrue(self.api.render_preview(PLACEMENT)["ok"])
        FakeFlasher.on_piece = None
        self.assertTrue(self.api.prepare_flash(PLACEMENT, None)["ok"])  # a new flash starts un-stopped
        self.assertTrue(self.api.confirm_flash()["ok"])

    def test_can_close_when_idle(self):
        window = FakeWindow()
        self.api._window = window
        self.assertTrue(self.api.can_close())
        time.sleep(0.05)
        self.assertEqual(window.js, [])

    def test_close_blocked_while_flashing(self):
        window = FakeWindow()
        self.api._window = window
        self.api.open_gif(str(self.gif))
        self.assertTrue(self.api.prepare_flash(PLACEMENT, None)["ok"])
        with self.assertLogs("pixelboard.app", "INFO"):
            self.assertFalse(self.api.can_close())
        self.assertIn(f"window.studio.onCloseBlocked({json.dumps(MSG_LOCKED)})", window.wait_js())
        self.assertTrue(self.api.confirm_flash()["ok"])
        self.assertTrue(self.api.can_close())

    def test_close_blocked_ui_error_is_logged(self):
        self.api._window = FakeWindow(error=RuntimeError("window gone"))
        self.api.open_gif(str(self.gif))
        self.assertTrue(self.api.prepare_flash(PLACEMENT, None)["ok"])
        with self.assertLogs("pixelboard.app", "ERROR") as logs:
            self.assertFalse(self.api.can_close())
            deadline = time.monotonic() + 2
            while not any("ERROR" in line for line in logs.output) and time.monotonic() < deadline:
                time.sleep(0.005)
        self.assertTrue(self.api.cancel_flash()["ok"])
        self.assertTrue(self.api.can_close())

    def test_stop_flash_without_flash(self):
        self.assertTrue(self.api.stop_flash()["ok"])

    def test_progress_reports_stoppable(self):
        api = self.make_api(flasher_factory=blank_flasher)
        window = FakeWindow()
        api._window = window
        api.open_gif(str(self.gif))
        self.assertTrue(api.prepare_flash(PLACEMENT, None)["ok"])
        self.assertTrue(api.confirm_flash()["ok"])
        writes = [js for js in window.js if js.startswith('window.studio.onProgress("write"')]
        self.assertTrue(any(js.endswith(", false)") for js in writes), writes)
        self.assertTrue(any(js.endswith(", true)") for js in writes), writes)
        self.assertEqual(writes[-1], 'window.studio.onProgress("write", 1.000, true)')


KEY = "linux-x86_64"
NEW_ASSET = updater.Asset(
    "PixelBoard-Studio-1.2.58-linux-x86_64.AppImage",
    updater.DOWNLOAD_PREFIX + "v1.2.58/PixelBoard-Studio-1.2.58-linux-x86_64.AppImage", 3, "0" * 64)
MANIFEST = updater.Manifest("1.2.58", {KEY: NEW_ASSET})


def update_failed(reason):
    return {"ok": False, "message": f"更新失敗：{reason}，你可以繼續使用目前的版本"}


class FakeUpdater:
    """Stands in for updater.check/download/install; download can be held on `gate`."""

    def __init__(self, manifest=MANIFEST):
        self.manifest = manifest
        self.check_calls, self.check_threads, self.downloads, self.installs = [], [], [], []
        self.check_error = self.download_error = self.install_error = None
        self.gate = None
        self.started = threading.Event()

    def check(self, current):
        self.check_calls.append(current)
        self.check_threads.append(threading.current_thread())
        if self.check_error:
            raise self.check_error
        return self.manifest

    def download(self, asset, dest_dir, progress):
        self.started.set()
        for f in (0.0, 0.001, 0.002, 0.5, 0.505, 1.0):
            progress(f)
        if self.gate is not None:
            self.gate.wait(5)
        if self.download_error:
            raise self.download_error
        path = pathlib.Path(dest_dir) / asset.name
        path.write_bytes(b"new")
        self.downloads.append((asset, path))
        return path

    def install(self, downloaded, key, exe_path):
        self.installs.append((pathlib.Path(downloaded).read_bytes(), key, exe_path))
        if self.install_error:
            raise self.install_error


class UpdateApiTest(ApiTestCase):
    def setUp(self):
        super().setUp()
        self.exe = self.dir / "PixelBoard-Studio.AppImage"
        self.fake = FakeUpdater()
        self.api = self.update_api()
        self.window = FakeWindow()
        self.api._window = self.window

    def update_api(self, fake=None, **kwargs):
        fake = fake or self.fake
        args = dict(updater_check=fake.check, updater_download=fake.download, updater_install=fake.install,
                    current_version="1.2.57", platform_key=lambda: KEY, executable=lambda: self.exe)
        args.update(kwargs)
        api = self.make_api(**args)
        api._quit = mock.Mock()
        return api

    def progress_js(self):
        return [js for js in self.window.js if js.startswith("window.studio.onUpdateProgress(")]

    def test_check_update_available(self):
        res = self.api.check_update()
        self.assertEqual(res, {"ok": True, "available": True, "version": "1.2.58", "current": "1.2.57",
                               "has_work": False, "checking": False, "flashing": False, "updating": False})
        self.assertEqual(self.fake.check_calls, ["1.2.57"])
        self.assertIsNot(self.fake.check_threads[0], threading.current_thread())  # background thread
        self.assertTrue(self.api.check_update()["available"])
        self.assertEqual(self.fake.check_calls, ["1.2.57"])  # checked once per session
        self.assertEqual(self.api.get_update_state(), res)

    def test_check_update_none_or_error(self):
        api = self.update_api(FakeUpdater(manifest=None))
        res = api.check_update()
        self.assertEqual((res["ok"], res["available"], res["version"], res["checking"]), (True, False, "", False))
        broken = FakeUpdater()
        broken.check_error = RuntimeError("boom")
        api = self.update_api(broken)
        with self.assertLogs("pixelboard.app", "ERROR"):
            res = api.check_update()
        self.assertEqual((res["ok"], res["available"]), (True, False))

    def test_check_update_still_running(self):
        gate = threading.Event()
        fake = FakeUpdater()
        fake.check = lambda current: gate.wait(5) and MANIFEST
        api = self.update_api(fake)
        with mock.patch.object(app_module, "UPDATE_CHECK_WAIT_S", 0.01):
            res = api.check_update()
        self.assertEqual((res["ok"], res["available"], res["checking"]), (True, False, True))
        gate.set()
        deadline = time.monotonic() + 2
        while api.get_update_state()["checking"] and time.monotonic() < deadline:
            time.sleep(0.005)
        self.assertTrue(api.get_update_state()["available"])

    def test_update_deferred_while_flashing(self):
        self.api.open_gif(str(self.gif))
        self.assertTrue(self.api.prepare_flash(PLACEMENT, None)["ok"])
        self.assertEqual(self.api.start_update(), {"ok": False, "message": MSG_UPDATE_WHILE_FLASHING})
        self.assertEqual(MSG_UPDATE_WHILE_FLASHING, "燒錄中不能更新，請等燒錄結束")
        res = self.api.check_update()
        self.assertTrue(res["available"])
        self.assertTrue(res["flashing"])
        self.assertEqual(self.fake.downloads, [])
        self.assertTrue(self.api.confirm_flash()["ok"])
        state = self.api.get_update_state()
        self.assertEqual((state["available"], state["flashing"], state["has_work"]), (True, False, True))
        self.assertTrue(self.api.start_update()["ok"])

    def test_flash_rejected_while_updating(self):
        self.api.open_gif(str(self.gif))
        mp3 = self.write("a.mp3", fx.stereo_44k(3))
        self.api.add_tracks([mp3, mp3])
        self.api.check_update()
        self.fake.gate = threading.Event()
        result = {}
        worker = threading.Thread(target=lambda: result.update(self.api.start_update()))
        worker.start()
        try:
            self.assertTrue(self.fake.started.wait(2))
            busy = {"ok": False, "message": MSG_UPDATING}
            self.assertEqual(MSG_UPDATING, "正在更新程式，請稍候")
            self.assertEqual(self.api.prepare_flash(PLACEMENT, None), busy)
            self.assertEqual(self.api.prepare_flash(None, None, firmware_only=True), busy)
            self.assertEqual(self.api.confirm_flash(), busy)
            self.assertEqual(FakeFlasher.instances, [])
            edits = {
                "open_gif": lambda: self.api.open_gif(str(self.gif)),
                "add_tracks": lambda: self.api.add_tracks([mp3]),
                "remove_track": lambda: self.api.remove_track(0),
                "move_track": lambda: self.api.move_track(0, 1),
                "set_panel_count": lambda: self.api.set_panel_count(2),
                "apply_preset": lambda: self.api.apply_preset("column"),
                "move_panel": lambda: self.api.move_panel(0, 10, 10),
                "rotate_panel": lambda: self.api.rotate_panel(0),
                "render_preview": lambda: self.api.render_preview(PLACEMENT),
                "identify": lambda: self.api.identify("COM11"),
                "start_update": self.api.start_update,
            }
            for name, call in edits.items():
                self.assertEqual(call(), busy, name)
            self.assertEqual(self.identify_calls, [])
            self.assertTrue(self.api.get_update_state()["updating"])
            with self.assertLogs("pixelboard.app", "INFO"):
                self.assertFalse(self.api.can_close())
            self.assertIn(f"window.studio.onCloseBlocked({json.dumps(MSG_UPDATING)})",
                          self.window.wait_js(len(self.progress_js()) + 1))
        finally:
            self.fake.gate.set()
            worker.join(5)
        self.assertTrue(result["ok"])
        self.api._quit.assert_called_once_with()

    def test_update_failure_message_and_unlock(self):
        self.api.open_gif(str(self.gif))
        self.api.check_update()
        cases = [
            ("download", updater.UpdateError(updater.MSG_NETWORK, "timed out"), updater.MSG_NETWORK),
            ("install", updater.UpdateError(updater.MSG_INSTALL, "rename failed"), updater.MSG_INSTALL),
            ("install", RuntimeError("boom"), "發生未預期的錯誤"),
        ]
        for step, error, reason in cases:
            with self.subTest(step=step, reason=reason):
                self.fake.download_error = error if step == "download" else None
                self.fake.install_error = error if step == "install" else None
                with self.assertLogs("pixelboard.app", "WARNING"):
                    res = self.api.start_update()
                self.assertEqual(res, update_failed(reason))
                self.api._quit.assert_not_called()
                state = self.api.get_update_state()
                self.assertEqual((state["updating"], state["available"]), (False, True))
                for _, path in self.fake.downloads:
                    self.assertFalse(path.parent.exists(), path)  # the download folder is removed
                self.assertTrue(self.api.can_close())
                self.assertTrue(self.api.render_preview(PLACEMENT)["ok"])
        self.assertEqual(len(self.fake.installs), 2)
        self.assertTrue(self.api.prepare_flash(PLACEMENT, None)["ok"])
        self.assertTrue(self.api.confirm_flash()["ok"])

    def test_update_success_quits(self):
        self.api.check_update()
        res = self.api.start_update()
        self.assertTrue(res["ok"])
        self.assertEqual(self.fake.installs, [(b"new", KEY, self.exe)])
        self.api._quit.assert_called_once_with()
        (_, path), = self.fake.downloads
        self.assertFalse(path.parent.exists())
        self.assertEqual(self.progress_js(), ["window.studio.onUpdateProgress(0.000)",
                                              "window.studio.onUpdateProgress(0.500)",
                                              "window.studio.onUpdateProgress(1.000)"])

    def test_update_progress_throttled_by_time(self):
        api = self.update_api()
        api._window = self.window

        def download(asset, dest_dir, progress):
            for fraction, step in ((0.1, 0), (0.101, 0.25), (0.102, 0.05), (0.103, 0.2), (0.104, 0)):
                progress(fraction)  # sent: first, after 200 ms, else dropped (< 1 % change)
                self.clock.now += step
            return self.fake.download(asset, dest_dir, lambda f: None)

        api._updater_download = download
        api.check_update()
        self.assertTrue(api.start_update()["ok"])
        self.assertEqual(self.progress_js(), ["window.studio.onUpdateProgress(0.100)",
                                              "window.studio.onUpdateProgress(0.102)",
                                              "window.studio.onUpdateProgress(0.104)"])

    def test_has_work_flag(self):
        self.assertFalse(self.api.check_update()["has_work"])
        self.api.open_gif(str(self.gif))
        self.assertTrue(self.api.get_update_state()["has_work"])
        api = self.update_api()
        api.add_tracks([self.write("a.mp3", fx.stereo_44k(3))])
        self.assertTrue(api.check_update()["has_work"])
        api.remove_track(0)
        self.assertFalse(api.get_update_state()["has_work"])

    def test_update_restart_failure_keeps_running(self):
        self.api.check_update()
        self.fake.install_error = updater.UpdateError(updater.MSG_RESTART, "spawn failed")
        with self.assertLogs("pixelboard.app", "WARNING"):
            res = self.api.start_update()
        self.assertEqual(res, {"ok": False, "installed": True, "message": updater.MSG_RESTART})
        self.api._quit.assert_not_called()
        state = self.api.get_update_state()
        self.assertEqual((state["updating"], state["available"]), (False, False))
        self.assertTrue(self.api.can_close())
        self.assertFalse(self.api.start_update()["ok"])
        self.assertEqual(len(self.fake.installs), 1)

    def test_open_url_false_means_no_browser(self):
        with mock.patch.object(app_module.webbrowser, "open", return_value=False):
            with self.assertRaises(OSError):
                app_module._open_url(updater.RELEASES_URL)
        with mock.patch.object(app_module.webbrowser, "open", return_value=True) as opened:
            app_module._open_url(updater.RELEASES_URL)
        opened.assert_called_once_with(updater.RELEASES_URL)

    def test_update_no_permission_messages(self):
        for browser_ok, reason in ((False, updater.MSG_NO_PERMISSION_NO_BROWSER),
                                   (True, updater.MSG_NO_PERMISSION)):
            with self.subTest(browser_ok=browser_ok):
                api = self.update_api(updater_install=None)  # the real updater.install
                api.check_update()
                with mock.patch.object(updater, "_dir_writable", return_value=False), \
                        mock.patch.object(app_module.webbrowser, "open", return_value=browser_ok) as opened, \
                        self.assertLogs("pixelboard", "WARNING"):
                    res = api.start_update()
                opened.assert_called_once_with(updater.RELEASES_URL)
                self.assertEqual(res, update_failed(reason))
                if not browser_ok:
                    self.assertNotIn("已打開", res["message"])
                    self.assertIn(updater.RELEASES_URL, res["message"])
                api._quit.assert_not_called()

    def test_default_install_uses_detached_spawn(self):
        api = self.update_api(updater_install=None)
        api.check_update()
        with mock.patch.object(updater, "install") as install:
            self.assertTrue(api.start_update()["ok"])
        (downloaded, key, exe), kwargs = install.call_args
        self.assertEqual((downloaded.name, key, exe), (NEW_ASSET.name, KEY, self.exe))
        self.assertIs(kwargs["spawn"], updater.spawn_detached)
        self.assertIs(kwargs["open_url"], app_module._open_url)

    def test_update_from_source_rejected(self):
        api = self.update_api(executable=lambda: None)
        api.check_update()
        with self.assertLogs("pixelboard.app", "WARNING"):
            res = api.start_update()
        self.assertEqual(res, update_failed("目前是從原始碼執行，無法自動更新"))
        self.assertEqual(self.fake.downloads, [])
        self.assertFalse(api.get_update_state()["updating"])
        with mock.patch.object(updater, "current_executable", return_value=None) as current:
            api = self.update_api(executable=None)
        api.check_update()
        with self.assertLogs("pixelboard.app", "WARNING"):
            self.assertFalse(api.start_update()["ok"])
        current.assert_called()

    def test_start_update_without_new_version(self):
        api = self.update_api(FakeUpdater(manifest=None))
        api.check_update()
        self.assertEqual(api.start_update(), {"ok": False, "message": "目前沒有可以安裝的新版本"})
        self.assertFalse(api.get_update_state()["updating"])

    def test_quit_destroys_window_and_close_allowed(self):
        api = self.make_api(updater_check=self.fake.check, updater_download=self.fake.download,
                            updater_install=self.fake.install, current_version="1.2.57",
                            platform_key=lambda: KEY, executable=lambda: self.exe)
        window = FakeWindow(on_destroy=lambda: api.can_close())
        api._window = window
        api.check_update()
        self.assertTrue(api.start_update()["ok"])
        self.assertIs(window.destroyed, True)  # can_close() let the destroy() through


class RunStudioCleanupTest(unittest.TestCase):
    def test_cleanup_old_at_startup(self):
        import run_studio

        exe = pathlib.Path("/opt/PixelBoard-Studio.AppImage")
        with mock.patch.object(updater, "current_executable", return_value=exe), \
                mock.patch.object(updater, "cleanup_old") as cleanup:
            run_studio._cleanup_old_update()
        cleanup.assert_called_once_with(exe)
        with mock.patch.object(updater, "current_executable", return_value=None), \
                mock.patch.object(updater, "cleanup_old") as cleanup:
            run_studio._cleanup_old_update()
        cleanup.assert_not_called()
        with mock.patch.object(updater, "current_executable", return_value=exe), \
                mock.patch.object(updater, "cleanup_old", side_effect=RuntimeError("boom")), \
                self.assertLogs("studio", "ERROR"):
            run_studio._cleanup_old_update()  # never crashes the start


if __name__ == "__main__":
    unittest.main()
