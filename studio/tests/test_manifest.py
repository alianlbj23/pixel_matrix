"""studio/ci/make_manifest.py: latest.json + SHA256SUMS for a release (round-trips through the updater)."""
import hashlib
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

from pixelboard import updater

STUDIO = pathlib.Path(__file__).resolve().parent.parent
MAKE_MANIFEST = STUDIO / "ci" / "make_manifest.py"
VER = "1.2.57"
PREFIX = f"https://github.com/alianlbj23/pixel_matrix/releases/download/v{VER}/"
NAMES = {
    "windows-x64": f"PixelBoard-Studio-{VER}-windows-x64.exe",
    "macos-arm64": f"PixelBoard-Studio-{VER}-macos-arm64.zip",
    "macos-x86_64": f"PixelBoard-Studio-{VER}-macos-x86_64.zip",
    "linux-x86_64": f"PixelBoard-Studio-{VER}-linux-x86_64.AppImage",
}


def _run(*args) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(MAKE_MANIFEST), *map(str, args)],
                          capture_output=True, text=True, timeout=60)


class MakeManifestTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = pathlib.Path(tmp.name)
        self.assets = self.root / "assets"
        self.out = self.root / "out"
        self.assets.mkdir()
        self.payloads = {}
        for key, name in NAMES.items():
            payload = f"fake {key} build ".encode() * (len(key) + 3)
            (self.assets / name).write_bytes(payload)
            self.payloads[key] = payload
        # Files that are not release assets (e.g. lock files) must be ignored.
        (self.assets / "requirements-lock-linux-x86_64.txt").write_text("x==1\n", encoding="utf-8")

    def _release_assets(self) -> dict:
        names = list(NAMES.values()) + ["latest.json", "SHA256SUMS"]
        return {n: PREFIX + n for n in names}

    def test_latest_json_round_trips_through_parse_manifest(self):
        r = _run(VER, self.assets, self.out)
        self.assertEqual(r.returncode, 0, r.stderr)
        manifest = updater.parse_manifest((self.out / "latest.json").read_bytes(), self._release_assets())
        self.assertEqual(manifest.version, VER)
        self.assertEqual(set(manifest.assets), set(NAMES))
        for key, asset in manifest.assets.items():
            self.assertEqual(asset.name, NAMES[key])
            self.assertEqual(asset.url, PREFIX + NAMES[key])
            self.assertEqual(asset.size, len(self.payloads[key]))
            self.assertEqual(asset.sha256, hashlib.sha256(self.payloads[key]).hexdigest())

    def test_sha256sums_format(self):
        r = _run(VER, self.assets, self.out)
        self.assertEqual(r.returncode, 0, r.stderr)
        lines = (self.out / "SHA256SUMS").read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(lines), len(NAMES))
        seen = {}
        for line in lines:
            m = re.fullmatch(r"([0-9a-f]{64})  (\S+)", line)
            self.assertIsNotNone(m, line)
            seen[m.group(2)] = m.group(1)
        self.assertEqual(seen, {NAMES[k]: hashlib.sha256(p).hexdigest() for k, p in self.payloads.items()})

    @unittest.skipUnless(shutil.which("sha256sum"), "sha256sum not installed")
    def test_sha256sums_verifies_with_sha256sum(self):
        r = _run(VER, self.assets, self.out)
        self.assertEqual(r.returncode, 0, r.stderr)
        check = subprocess.run(["sha256sum", "-c", str(self.out / "SHA256SUMS")], cwd=self.assets,
                               capture_output=True, text=True, timeout=60)
        self.assertEqual(check.returncode, 0, check.stdout + check.stderr)

    def test_missing_platform_exits_1(self):
        for key, name in NAMES.items():
            with self.subTest(key=key):
                (self.assets / name).rename(self.root / name)
                try:
                    r = _run(VER, self.assets, self.out)
                finally:
                    (self.root / name).rename(self.assets / name)
                self.assertEqual(r.returncode, 1)
                self.assertIn(name, r.stderr)
                self.assertFalse((self.out / "latest.json").exists())
                self.assertFalse((self.out / "SHA256SUMS").exists())

    def test_empty_asset_exits_1(self):
        (self.assets / NAMES["linux-x86_64"]).write_bytes(b"")
        r = _run(VER, self.assets, self.out)
        self.assertEqual(r.returncode, 1)
        self.assertFalse((self.out / "latest.json").exists())

    def test_invalid_version_exits_1(self):
        for bad in ["1.2", "1.2.57-dev", "v1.2.57", "1.2.57\n"]:
            with self.subTest(bad=bad):
                r = _run(bad, self.assets, self.out)
                self.assertEqual(r.returncode, 1)
                self.assertFalse((self.out / "latest.json").exists())

    def test_assets_dir_missing_exits_1(self):
        r = _run(VER, self.root / "nope", self.out)
        self.assertEqual(r.returncode, 1)

    def test_wrong_arg_count_prints_usage(self):
        r = _run(VER, self.assets)
        self.assertEqual(r.returncode, 2)
        self.assertIn("usage", r.stderr)


if __name__ == "__main__":
    unittest.main()
