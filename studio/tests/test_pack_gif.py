import importlib.util
import pathlib
import tempfile
import unittest

from PIL import Image

from pixelboard import pxg

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
        rc = self.tool.main([str(src), str(out), "--chain", "2", "--brightness", "70", "--swap"])
        self.assertEqual(rc, 0)
        anim = pxg.unpack(out.read_bytes())
        self.assertEqual((anim.chain, anim.brightness, anim.swap, len(anim.frames)), (2, 70, False, 1))
        self.assertEqual(anim.panels, [(64, 0, 0), (0, 0, 0)])  # --swap = panel order reversed

    def test_bad_input_returns_1_and_writes_nothing(self):
        out = self.dir / "x.pxg"
        rc = self.tool.main([str(self.dir / "missing.gif"), str(out)])
        self.assertEqual(rc, 1)
        self.assertFalse(out.exists())

    def test_split_needs_chain_2(self):
        src = self.dir / "a.gif"
        Image.new("RGB", (10, 10), (255, 0, 0)).save(src)
        self.assertEqual(self.tool.main([str(src), str(self.dir / "o.pxg"), "--split"]), 1)


if __name__ == "__main__":
    unittest.main()
