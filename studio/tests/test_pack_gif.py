import importlib.util
import json
import pathlib
import tempfile
import unittest

from PIL import Image

from pixelboard import layout, pxg

_TOOL = pathlib.Path(__file__).resolve().parents[2] / "tools" / "pack_gif.py"


def load_tool():
    spec = importlib.util.spec_from_file_location("pack_gif", _TOOL)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class PackGifCliTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = pathlib.Path(self._tmp.name)
        self.tool = load_tool()

    def tearDown(self):
        self._tmp.cleanup()

    def test_writes_pxg_with_options(self):
        src = self.dir / "a.gif"
        Image.new("RGB", (10, 10), (255, 0, 0)).save(src)
        out = self.dir / "a.pxg"
        rc = self.tool.main([str(src), str(out), "--chain", "2", "--brightness", "70"])
        self.assertEqual(rc, 0)
        anim = pxg.unpack(out.read_bytes())
        self.assertEqual((anim.chain, anim.brightness, len(anim.frames)), (2, 70, 1))
        self.assertEqual(anim.panels, [(0, 0, 0), (64, 0, 0)])

    def test_layout_file_used(self):
        src = self.dir / "a.gif"
        Image.new("RGB", (10, 10), (255, 0, 0)).save(src)
        arr = layout.preset("snake", 4)
        lay = self.dir / "layout.json"
        lay.write_text(json.dumps(layout.to_dict(arr)))
        out = self.dir / "a.pxg"
        self.assertEqual(self.tool.main([str(src), str(out), "--layout", str(lay)]), 0)
        anim = pxg.unpack(out.read_bytes())
        self.assertEqual(anim.chain, 4)
        want = layout.normalize(arr)
        self.assertEqual(anim.panels, [(p.x, p.y, p.rotation) for p in want.panels])

    def test_bad_layout_file_returns_1(self):
        src = self.dir / "a.gif"
        Image.new("RGB", (10, 10), (255, 0, 0)).save(src)
        out = self.dir / "a.pxg"
        for content in ("not json", '{"panels": 5}', None):
            lay = self.dir / "layout.json"
            if content is None:
                lay = self.dir / "missing.json"
            else:
                lay.write_text(content)
            self.assertEqual(self.tool.main([str(src), str(out), "--layout", str(lay)]), 1)
            self.assertFalse(out.exists())

    def test_chain_out_of_range_returns_1(self):
        src = self.dir / "a.gif"
        Image.new("RGB", (10, 10), (255, 0, 0)).save(src)
        self.assertEqual(self.tool.main([str(src), str(self.dir / "o.pxg"), "--chain", "9"]), 1)

    def test_bad_input_returns_1_and_writes_nothing(self):
        out = self.dir / "x.pxg"
        rc = self.tool.main([str(self.dir / "missing.gif"), str(out)])
        self.assertEqual(rc, 1)
        self.assertFalse(out.exists())

    def test_split_needs_grid_arrangement(self):
        src = self.dir / "a.gif"
        Image.new("RGB", (10, 10), (255, 0, 0)).save(src)
        lay = self.dir / "layout.json"
        arr = layout.from_dict({"panels": [{"index": 0, "x": 0, "y": 0, "rotation": 0},
                                           {"index": 1, "x": 64, "y": 0, "rotation": 90}]})
        lay.write_text(json.dumps(layout.to_dict(arr)))
        self.assertEqual(self.tool.main([str(src), str(self.dir / "o.pxg"), "--layout", str(lay), "--split"]), 1)

    def test_split_row_2_ok(self):
        src = self.dir / "a.gif"
        Image.new("RGB", (20, 10), (255, 0, 0)).save(src)
        self.assertEqual(self.tool.main([str(src), str(self.dir / "o.pxg"), "--chain", "2", "--split"]), 0)


if __name__ == "__main__":
    unittest.main()
