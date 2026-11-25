"""studio/ci/check_release_version.py: a release must be strictly newer than the current latest release."""
import pathlib
import subprocess
import sys
import unittest

STUDIO = pathlib.Path(__file__).resolve().parent.parent
CHECK = STUDIO / "ci" / "check_release_version.py"


def _run(*args) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(CHECK), *args], capture_output=True, text=True, timeout=60)


class CheckReleaseVersionTests(unittest.TestCase):
    def test_newer_than_latest_ok(self):
        for new, latest in (("1.2.58", "v1.2.57"), ("1.3.1", "v1.2.99"), ("2.0.5", "v1.9.100"),
                            ("1.10.3", "v1.9.200"), ("1.2.58", "1.2.57")):
            with self.subTest(new=new, latest=latest):
                r = _run(new, latest)
                self.assertEqual(r.returncode, 0, r.stderr)
                self.assertIn(new, r.stdout)

    def test_no_release_yet_ok(self):
        r = _run("1.2.1", "")
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_equal_or_older_fails(self):
        for new, latest in (("1.2.57", "v1.2.57"), ("1.2.56", "v1.2.57"), ("1.2.100", "v1.3.2"),
                            ("1.9.500", "v1.10.1")):
            with self.subTest(new=new, latest=latest):
                r = _run(new, latest)
                self.assertEqual(r.returncode, 1)
                self.assertIn("not newer", r.stderr)

    def test_invalid_versions_fail(self):
        for new, latest in (("1.2.58-dev", "v1.2.57"), ("1.2", ""), ("", "v1.2.57"),
                            ("1.2.58", "v1.2.57-beta"), ("1.2.58", "latest"), ("1.2.58", "vv1.2.57"),
                            ("١.٢.٥٨", "")):
            with self.subTest(new=new, latest=latest):
                r = _run(new, latest)
                self.assertEqual(r.returncode, 1)
                self.assertIn("invalid", r.stderr)

    def test_usage(self):
        for args in ((), ("1.2.58",), ("1.2.58", "v1.2.57", "extra")):
            with self.subTest(args=args):
                r = _run(*args)
                self.assertEqual(r.returncode, 2)
                self.assertIn("usage", r.stderr)


if __name__ == "__main__":
    unittest.main()
