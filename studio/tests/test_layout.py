import json
import os
import random
import tempfile
import unittest
from unittest import mock

from pixelboard import layout
from pixelboard.layout import Arrangement, LayoutError, Panel


def arr(*panels):
    return Arrangement(tuple(Panel(i, x, y, r) for i, (x, y, r) in enumerate(panels)))


class LayoutTest(unittest.TestCase):
    def test_panel_size(self):
        self.assertEqual(layout.panel_size(0), (64, 32))
        self.assertEqual(layout.panel_size(180), (64, 32))
        self.assertEqual(layout.panel_size(90), (32, 64))
        self.assertEqual(layout.panel_size(270), (32, 64))

    def test_validate_rejects(self):
        layout.validate(arr((0, 0, 0)))
        bad = [
            Arrangement(()),
            Arrangement(tuple(Panel(i, 64 * i, 0, 0) for i in range(9))),
            Arrangement((Panel(0, 0, 0, 0), Panel(0, 64, 0, 0))),
            arr((0, 0, 45)),
            arr((0, 0, 0), (32, 0, 0)),
            arr((5000, 0, 0)),
        ]
        for a in bad:
            with self.subTest(a=a):
                with self.assertRaises(LayoutError) as cm:
                    layout.validate(a)
                self.assertTrue(cm.exception.user_message)

    def test_touching_is_not_overlap(self):
        layout.validate(arr((0, 0, 0), (64, 0, 0), (0, 32, 0)))

    def test_presets(self):
        self.assertEqual([p.x for p in layout.preset("row", 3).panels], [0, 64, 128])
        self.assertEqual([p.y for p in layout.preset("column", 2).panels], [0, 32])
        g = layout.preset("grid", 4)
        self.assertEqual([(p.x, p.y) for p in g.panels], [(0, 0), (64, 0), (0, 32), (64, 32)])
        self.assertEqual(layout.grid_shape(g)[:2], (2, 2))
        s = layout.preset("snake", 4)
        self.assertEqual((s.panels[2].x, s.panels[2].rotation), (64, 180))
        self.assertEqual((s.panels[3].x, s.panels[3].rotation), (0, 180))
        self.assertEqual(s.panels[0].rotation, 0)
        with self.assertRaises(LayoutError):
            layout.preset("nope", 2)

    def test_snap_right_edge(self):
        r = layout.snap(layout.preset("row", 2), 1, 70, 5)
        self.assertEqual((r.panels[1].x, r.panels[1].y), (64, 0))

    def test_snap_below(self):
        r = layout.snap(layout.preset("row", 2), 1, 3, 36)
        self.assertEqual((r.panels[1].x, r.panels[1].y), (0, 32))

    def test_snap_overlap_reverts(self):
        a = layout.preset("row", 2)
        self.assertEqual(layout.snap(a, 1, 10, 0), a)

    def test_resize_adds_right_and_removes_highest(self):
        a = layout.resize(layout.preset("row", 2), 3)
        self.assertEqual(a.n, 3)
        self.assertEqual(a.panels[2], Panel(2, 128, 0, 0))
        b = layout.resize(a, 1)
        self.assertEqual(b.panels, (Panel(0, 0, 0, 0),))
        with self.assertRaises(LayoutError):
            layout.resize(a, 9)
        with self.assertRaises(LayoutError):
            layout.resize(a, 0)

    def test_grid_shape_with_gaps(self):
        self.assertEqual(layout.grid_shape(arr((0, 0, 0), (80, 0, 0))), (1, 2, [0, 1]))
        self.assertIsNone(layout.grid_shape(arr((0, 0, 0), (64, 0, 0), (0, 32, 0))))
        self.assertIsNone(layout.grid_shape(arr((0, 0, 0), (64, 0, 90))))

    def test_chain_roundtrip_all_rotations(self):
        a = arr((0, 0, 0), (64, 0, 90), (0, 64, 180), (96, 64, 270))
        cw, ch = layout.bounds(a)
        self.assertEqual((cw, ch), (128, 128))
        rng = random.Random(1)
        canvas = bytes(rng.randrange(256) for _ in range(cw * ch * 3))
        chain = layout.chain_frame(canvas, cw, ch, a)
        self.assertEqual(len(chain), 64 * 4 * 32 * 3)
        expected = bytearray(cw * ch * 3)
        for p in a.panels:
            for ly in range(32):
                for lx in range(64):
                    cx, cy = {
                        0: (p.x + lx, p.y + ly),
                        90: (p.x + 31 - ly, p.y + lx),
                        180: (p.x + 63 - lx, p.y + 31 - ly),
                        270: (p.x + ly, p.y + 63 - lx),
                    }[p.rotation]
                    o = (cy * cw + cx) * 3
                    expected[o:o + 3] = canvas[o:o + 3]
        out, ocw, och = layout.canvas_frame(chain, a)
        self.assertEqual((ocw, och), (cw, ch))
        self.assertEqual(out, bytes(expected))

    def test_rotation_90_mapping(self):
        a = arr((0, 0, 90))
        p = a.panels[0]
        self.assertEqual(layout.canvas_to_panel(p, 31, 0), (0, 0))
        self.assertEqual(layout.canvas_to_panel(p, 0, 63), (63, 31))
        self.assertIsNone(layout.canvas_to_panel(p, 32, 0))

    def test_load_corrupt_falls_back(self):
        overlap = {"version": 1, "panels": [
            {"index": 0, "x": 0, "y": 0, "rotation": 0},
            {"index": 1, "x": 10, "y": 0, "rotation": 0}]}
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "layout.json")
            self.assertEqual(layout.load(path).n, 1)
            for content in ("not json", json.dumps({"panels": []}), json.dumps(overlap)):
                with open(path, "w") as f:
                    f.write(content)
                with self.assertLogs("pixelboard.layout", level="WARNING"):
                    self.assertEqual(layout.load(path).n, 1)

    def test_load_deeply_nested_falls_back(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "layout.json")
            with open(path, "w") as f:
                f.write("[" * 200000 + "]" * 200000)
            with self.assertLogs("pixelboard.layout", level="WARNING"):
                self.assertEqual(layout.load(path).n, 1)

    def test_from_dict_rejects_bad_types(self):
        for d in (
            [], {"panels": "x"},
            {"version": 1, "panels": [{"index": 0, "x": True, "y": 0, "rotation": 0}]},
            {"version": 1, "panels": [{"index": 0, "x": 0.5, "y": 0, "rotation": 0}]},
            {"version": 1, "panels": [{"index": 0, "x": 0, "y": 0}]},
        ):
            with self.subTest(d=d):
                with self.assertRaises(LayoutError):
                    layout.from_dict(d)

    def test_save_load_roundtrip(self):
        a = layout.preset("snake", 5)
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "sub", "layout.json")
            layout.save(path, a)
            self.assertEqual(layout.load(path), a)

    def test_save_replaces_atomically(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "layout.json")
            with mock.patch("pixelboard.layout.os.replace", wraps=os.replace) as replace:
                layout.save(path, layout.preset("row", 2))
            replace.assert_called_once()
            src, dst = replace.call_args[0]
            self.assertEqual(os.path.dirname(os.fspath(src)), d)   # temp file in the same directory
            self.assertEqual(os.fspath(dst), path)
            self.assertEqual(os.listdir(d), ["layout.json"])

    def test_failed_save_keeps_old_file(self):
        old = layout.preset("row", 3)
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "layout.json")
            layout.save(path, old)
            for target, error in (("pixelboard.layout.os.replace", OSError(28, "No space left on device")),
                                  ("pixelboard.layout.json.dump", OSError(28, "No space left on device"))):
                with self.subTest(target=target):
                    with mock.patch(target, side_effect=error), \
                            self.assertLogs("pixelboard.layout", level="ERROR"), self.assertRaises(OSError):
                        layout.save(path, layout.preset("column", 2))
                    self.assertEqual(layout.load(path), old)
                    self.assertEqual(os.listdir(d), ["layout.json"])   # temp file removed


if __name__ == "__main__":
    unittest.main()
