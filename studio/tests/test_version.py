import pathlib
import subprocess
import sys
import tempfile
import unittest

from pixelboard import version

STUDIO = pathlib.Path(__file__).resolve().parent.parent
WRITE_VERSION = STUDIO / "packaging" / "write_version.py"


class VersionTests(unittest.TestCase):
    def test_parse_version_ok(self):
        self.assertEqual(version.parse_version("1.2.57"), (1, 2, 57))

    def test_parse_version_rejects(self):
        for bad in ["v1.2.3", "1.2", "1.2.57-dev", "", "1.2.x", "1.2.3.4", " 1.2.3",
                    "\u0661.\u0662.\u0665\u0668", "1.2.\uff15", "1.2.3\n"]:
            with self.subTest(bad=bad):
                self.assertIsNone(version.parse_version(bad))

    def test_is_newer(self):
        self.assertTrue(version.is_newer("1.2.58", "1.2.57"))
        self.assertTrue(version.is_newer("1.10.0", "1.9.9"))
        self.assertFalse(version.is_newer("1.2.57", "1.2.57"))
        self.assertFalse(version.is_newer("garbage", "1.2.57"))
        self.assertFalse(version.is_newer("1.3.0", "1.2.0-dev"))

    def test_current_version_dev(self):
        if (STUDIO / "pixelboard" / "_version.py").exists():
            self.skipTest("generated _version.py present")
        expected = (STUDIO / "VERSION").read_text().strip()
        cur = version.current_version()
        self.assertTrue(cur.endswith(".0-dev"))
        self.assertEqual(cur[: -len(".0-dev")], expected)
        self.assertTrue(version.is_dev_build())

    def test_write_version_ok(self):
        with tempfile.TemporaryDirectory() as d:
            out = pathlib.Path(d) / "_version.py"
            r = subprocess.run([sys.executable, str(WRITE_VERSION), "1.2.57", str(out)],
                               capture_output=True, timeout=30)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual(out.read_text(), 'APP_VERSION = "1.2.57"\n')

    def test_write_version_validates(self):
        with tempfile.TemporaryDirectory() as d:
            out = pathlib.Path(d) / "_version.py"
            r = subprocess.run([sys.executable, str(WRITE_VERSION), "1.2.x", str(out)],
                               capture_output=True, timeout=30)
            self.assertEqual(r.returncode, 1)
            self.assertFalse(out.exists())


if __name__ == "__main__":
    unittest.main()
