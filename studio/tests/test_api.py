import base64
import json
import pathlib
import tempfile
import unittest

from PIL import Image

from pixelboard import layout, pxa, pxg
from pixelboard.app import MSG_LOCKED, MSG_STOPPED, StudioApi
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

    def __init__(self, port, progress=None, connect_error=None, blank=False):
        super().__init__(port, progress)
        self.connect_error, self.blank = connect_error, blank
        self.pieces, self.closed, self.reset = [], False, False
        FakeFlasher.instances.append(self)

    def connect(self):
        if self.connect_error:
            raise self.connect_error
        self._esp = object()

    def read(self, offset, size):
        if self.blank:
            return b"\xff" * size
        return {0x10000: APP_HEAD, 0x8000: PART}[offset][:size]

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
    def __init__(self):
        self.js = []

    def evaluate_js(self, code):
        self.js.append(code)


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
        self.identify_error = BoardError("這塊板子還沒有新版韌體，請先按『安裝/更新韌體』")
        res = self.api.identify("COM11")
        self.assertEqual(res, {"ok": False, "message": "這塊板子還沒有新版韌體，請先按『安裝/更新韌體』"})
        self.identify_error = None
        self.assertTrue(self.api.identify("COM11")["ok"])  # a failure does not start the 5 s wait

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


if __name__ == "__main__":
    unittest.main()
