import contextlib
import hashlib
import io
import json
import os
import shlex
import shutil
import stat
import subprocess
import sys
import tempfile
import threading
import unittest
import urllib.error
import zipfile
from pathlib import Path
from typing import NamedTuple
from unittest import mock

from pixelboard import updater

TAG = "v1.2.58"
PREFIX = f"https://github.com/alianlbj23/pixel_matrix/releases/download/{TAG}/"
KEY = "linux-x86_64"
POSIX = os.name == "posix"


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def make_manifest(ver="1.2.58", keys=("windows-x64", "macos-arm64", "macos-x86_64", "linux-x86_64")):
    """Returns (latest.json bytes, release_assets {name: url}, payload by key)."""
    ext = {"windows-x64": ".exe", "macos-arm64": ".zip", "macos-x86_64": ".zip", "linux-x86_64": ".AppImage"}
    assets, release, payloads = {}, {}, {}
    for key in keys:
        name = f"PixelBoard-Studio-{ver}-{key}{ext[key]}"
        payload = f"binary for {key}".encode()
        assets[key] = {"name": name, "size": len(payload), "sha256": _sha(payload)}
        release[name] = PREFIX + name
        payloads[key] = payload
    release["latest.json"] = PREFIX + "latest.json"
    return json.dumps({"version": ver, "assets": assets}).encode(), release, payloads


def make_api(release_assets, tag=TAG):
    return json.dumps({
        "tag_name": tag,
        "assets": [{"name": n, "browser_download_url": u, "size": 1} for n, u in release_assets.items()],
    }).encode()


class FakeHttp:
    def __init__(self, responses):
        self.responses = responses
        self.calls = []

    def __call__(self, url, timeout):
        self.calls.append((url, timeout))
        resp = self.responses[url]
        if isinstance(resp, Exception):
            raise resp
        return resp


def fake_http_for(manifest_bytes, release, tag=TAG):
    return FakeHttp({updater.API_LATEST_URL: make_api(release, tag), release["latest.json"]: manifest_bytes})


class Recorder:
    def __init__(self, exc=None):
        self.calls = []
        self.exc = exc

    def __call__(self, *args):
        self.calls.append(args)
        if self.exc is not None:
            raise self.exc


def must_not_call(*args):
    raise AssertionError(f"unexpected call {args!r}")


class PlatformKeyTests(unittest.TestCase):
    def test_platform_key_all(self):
        self.assertEqual(updater.platform_key("win32", "AMD64"), "windows-x64")
        self.assertEqual(updater.platform_key("darwin", "arm64"), "macos-arm64")
        self.assertEqual(updater.platform_key("darwin", "x86_64"), "macos-x86_64")
        self.assertEqual(updater.platform_key("linux", "x86_64"), "linux-x86_64")

    def test_platform_key_unknown_raises(self):
        for system, machine in [("linux", "aarch64"), ("freebsd14", "x86_64"), ("darwin", "ppc"), ("", "")]:
            with self.subTest(system=system, machine=machine):
                with self.assertRaises(updater.UpdateError) as ctx:
                    updater.platform_key(system, machine)
                self.assertTrue(ctx.exception.user_message)


class ManifestTests(unittest.TestCase):
    def test_parse_manifest_ok(self):
        data, release, payloads = make_manifest()
        m = updater.parse_manifest(data, release)
        self.assertEqual(m.version, "1.2.58")
        self.assertEqual(set(m.assets), {"windows-x64", "macos-arm64", "macos-x86_64", "linux-x86_64"})
        a = m.assets[KEY]
        self.assertIsInstance(a, updater.Asset)
        self.assertEqual(a.name, "PixelBoard-Studio-1.2.58-linux-x86_64.AppImage")
        self.assertEqual(a.url, PREFIX + a.name)
        self.assertEqual(a.size, len(payloads[KEY]))
        self.assertEqual(a.sha256, _sha(payloads[KEY]))

    def test_parse_manifest_rejects(self):
        data, release, _ = make_manifest(keys=("linux-x86_64",))
        good = json.loads(data)
        name = good["assets"][KEY]["name"]

        def with_entry(**changes):
            doc = json.loads(data)
            doc["assets"][KEY].update(changes)
            return doc

        def without(field):
            doc = json.loads(data)
            del doc["assets"][KEY][field]
            return doc

        cases = {
            "missing version": {"assets": good["assets"]},
            "missing assets": {"version": "1.2.58"},
            "empty assets": {"version": "1.2.58", "assets": {}},
            "assets not dict": {"version": "1.2.58", "assets": [1]},
            "bad version": {"version": "1.2.58-beta", "assets": good["assets"]},
            "version not str": {"version": 1.2, "assets": good["assets"]},
            "missing name": without("name"),
            "missing size": without("size"),
            "missing sha256": without("sha256"),
            "size zero": with_entry(size=0),
            "size negative": with_entry(size=-5),
            "size over cap": with_entry(size=600 * 1024 * 1024 + 1),
            "size bool": with_entry(size=True),
            "size str": with_entry(size="12"),
            "sha short": with_entry(sha256="ab" * 31),
            "sha upper": with_entry(sha256="AB" * 32),
            "sha non hex": with_entry(sha256="zz" * 32),
            "name path": with_entry(name="../" + name),
            "name not in release": with_entry(name="other.AppImage"),
            "url in json ignored but name unknown": with_entry(url=PREFIX + "x", name="x"),
            "entry not dict": {"version": "1.2.58", "assets": {KEY: "nope"}},
            "top not dict": [good],
        }
        for label, doc in cases.items():
            with self.subTest(label):
                with self.assertRaises(updater.UpdateError):
                    updater.parse_manifest(json.dumps(doc).encode(), release)

        foreign = {"https://github.com/evil/pixel_matrix/releases/download/v1.2.58/" + name,
                   "http://github.com/alianlbj23/pixel_matrix/releases/download/v1.2.58/" + name,
                   "https://example.com/" + name,
                   PREFIX + "../../../../evil/repo/releases/download/v1/" + name,
                   PREFIX + name + "?redirect=https://example.com/"}
        for url in foreign:
            with self.subTest(url=url):
                with self.assertRaises(updater.UpdateError):
                    updater.parse_manifest(data, dict(release, **{name: url}))

        for broken in [b"", b"{not json", b"<html>rate limited</html>", b"\xff\xfe"]:
            with self.subTest(broken=broken):
                with self.assertRaises(updater.UpdateError):
                    updater.parse_manifest(broken, release)

    def test_parse_manifest_url_only_from_release(self):
        data, release, _ = make_manifest(keys=("linux-x86_64",))
        doc = json.loads(data)
        doc["assets"][KEY]["url"] = "https://example.com/evil"
        m = updater.parse_manifest(json.dumps(doc).encode(), release)
        self.assertTrue(m.assets[KEY].url.startswith(PREFIX))


class CheckTests(unittest.TestCase):
    def test_check_returns_newer(self):
        data, release, _ = make_manifest("1.2.58")
        http = fake_http_for(data, release)
        m = updater.check("1.2.57", http, key=KEY)
        self.assertIsNotNone(m)
        self.assertEqual(m.version, "1.2.58")
        self.assertIn(KEY, m.assets)
        self.assertEqual(http.calls, [(updater.API_LATEST_URL, 5.0), (PREFIX + "latest.json", 10.0)])

    def test_check_same_version_none(self):
        data, release, _ = make_manifest("1.2.57")
        self.assertIsNone(updater.check("1.2.57", fake_http_for(data, release, "v1.2.57"), key=KEY))

    def test_check_ignores_bad_version(self):
        for ver in ["1.2.58-beta", "v1.2.58", "1.2", "1.2.56"]:
            with self.subTest(ver=ver):
                data, release, _ = make_manifest("1.2.58")
                doc = json.loads(data)
                doc["version"] = ver
                http = fake_http_for(json.dumps(doc).encode(), release, "v" + ver)
                with self.assertLogs(updater.log, "INFO"):
                    self.assertIsNone(updater.check("1.2.57", http, key=KEY))
        data, release, _ = make_manifest("1.2.58")
        self.assertIsNone(updater.check("garbage", fake_http_for(data, release), key=KEY))

    def test_check_dev_build_none(self):
        self.assertIsNone(updater.check("1.2.0-dev", must_not_call, key=KEY))

    def test_check_offline_silent(self):
        http = FakeHttp({updater.API_LATEST_URL: urllib.error.URLError("no route to host")})
        with self.assertLogs(updater.log, "WARNING") as logs:
            self.assertIsNone(updater.check("1.2.57", http, key=KEY))
        self.assertTrue(any("no route to host" in line for line in logs.output))

    def test_check_bad_json_silent(self):
        data, release, _ = make_manifest("1.2.58")
        latest = release["latest.json"]
        bad_api = [
            b"<html>Too many requests</html>",
            b"[]",
            json.dumps({"tag_name": TAG}).encode(),
            json.dumps({"tag_name": TAG, "assets": "x"}).encode(),
            json.dumps({"tag_name": TAG, "assets": [1]}).encode(),
            json.dumps({"tag_name": TAG, "assets": [{"name": 1, "browser_download_url": latest}]}).encode(),
            json.dumps({"tag_name": TAG, "assets": [{"name": "latest.json", "browser_download_url": None}]}).encode(),
            json.dumps({"tag_name": TAG, "assets": [{"name": "x", "browser_download_url": PREFIX + "x"}]}).encode(),
            json.dumps({"tag_name": TAG, "assets": [
                {"name": "latest.json", "browser_download_url": "https://example.com/latest.json"}]}).encode(),
        ]
        for body in bad_api:
            with self.subTest(body=body[:60]):
                http = FakeHttp({updater.API_LATEST_URL: body, latest: data})
                with self.assertLogs(updater.log, "WARNING"):
                    self.assertIsNone(updater.check("1.2.57", http, key=KEY))
        http = FakeHttp({updater.API_LATEST_URL: make_api(release), latest: b"<html>oops</html>"})
        with self.assertLogs(updater.log, "WARNING"):
            self.assertIsNone(updater.check("1.2.57", http, key=KEY))

    def test_check_tag_mismatch_none(self):
        data, release, _ = make_manifest("1.2.58")
        with self.assertLogs(updater.log, "WARNING"):
            self.assertIsNone(updater.check("1.2.57", fake_http_for(data, release, "v1.2.59"), key=KEY))

    def test_check_missing_platform_none(self):
        data, release, _ = make_manifest("1.2.58", keys=("windows-x64",))
        with self.assertLogs(updater.log, "INFO"):
            self.assertIsNone(updater.check("1.2.57", fake_http_for(data, release), key=KEY))

    def test_check_unsupported_platform_none(self):
        data, release, _ = make_manifest("1.2.58")
        with mock.patch.object(updater, "platform_key", side_effect=updater.UpdateError("x")):
            self.assertIsNone(updater.check("1.2.57", fake_http_for(data, release)))


class Stream(io.BytesIO):
    """A fake download stream that can fail after `fail_after` bytes."""

    def __init__(self, data, fail_after=None):
        super().__init__(data)
        self.fail_after = fail_after

    def read(self, n=-1):
        if self.fail_after is not None and self.tell() >= self.fail_after:
            raise TimeoutError("read timed out")
        return super().read(n)


def asset_for(payload, name="PixelBoard-Studio-1.2.58-linux-x86_64.AppImage", size=None, sha=None):
    return updater.Asset(name, PREFIX + name, len(payload) if size is None else size,
                         _sha(payload) if sha is None else sha)


class DownloadTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dest = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_download_ok_progress(self):
        payload = bytes(range(256)) * 10
        asset = asset_for(payload)
        progress, opened = [], []

        def open_stream(url, timeout):
            opened.append((url, timeout))
            return Stream(payload)

        path = updater.download(asset, self.dest, open_stream, progress.append, chunk=100)
        self.assertEqual(path, self.dest / asset.name)
        self.assertEqual(path.read_bytes(), payload)
        self.assertEqual(opened, [(asset.url, updater.DOWNLOAD_TIMEOUT)])
        self.assertGreater(len(progress), 3)
        self.assertEqual(progress, sorted(progress))
        self.assertEqual(progress[0], 0.0)
        self.assertEqual(progress[-1], 1.0)
        self.assertEqual(os.listdir(self.dest), [asset.name])

    def _assert_corrupt(self, asset, stream):
        with self.assertRaises(updater.UpdateError) as ctx:
            updater.download(asset, self.dest, lambda url, timeout: stream, lambda f: None, chunk=7)
        self.assertIn("下載的檔案不完整或已損壞", ctx.exception.user_message)

    def test_download_sha_mismatch_keeps_old(self):
        payload = b"new version payload"
        asset = asset_for(payload, sha=_sha(b"something else"))
        old = self.dest / asset.name
        old.write_bytes(b"old download")
        self._assert_corrupt(asset, Stream(payload))
        self.assertEqual(old.read_bytes(), b"old download")
        self.assertEqual(os.listdir(self.dest), [asset.name])

    def test_download_truncated(self):
        payload = b"x" * 100
        self._assert_corrupt(asset_for(payload), Stream(payload[:60]))
        self.assertEqual(os.listdir(self.dest), [])

    def test_download_oversize(self):
        payload = b"x" * 100
        asset = asset_for(payload)
        self._assert_corrupt(asset, Stream(payload + b"extra"))
        self.assertEqual(os.listdir(self.dest), [])

    def test_download_network_error_cleans_up(self):
        payload = b"y" * 100
        with self.assertRaises(updater.UpdateError) as ctx:
            updater.download(asset_for(payload), self.dest, lambda url, timeout: Stream(payload, 30),
                             lambda f: None, chunk=10)
        self.assertTrue(ctx.exception.user_message)
        self.assertEqual(os.listdir(self.dest), [])

    def test_download_disk_errors(self):
        payload = b"d" * 50
        with self.subTest("missing dest dir"):
            with self.assertRaises(updater.UpdateError) as ctx:
                updater.download(asset_for(payload), self.dest / "missing", lambda url, timeout: Stream(payload),
                                 lambda f: None)
            self.assertIn("磁碟", ctx.exception.user_message)
        with self.subTest("write fails"):
            real_fdopen = os.fdopen

            def failing_fdopen(*args, **kwargs):
                f = real_fdopen(*args, **kwargs)
                f.write = mock.Mock(side_effect=OSError(28, "No space left on device"))
                return f

            with mock.patch.object(updater.os, "fdopen", failing_fdopen):
                with self.assertRaises(updater.UpdateError) as ctx:
                    updater.download(asset_for(payload), self.dest, lambda url, timeout: Stream(payload),
                                     lambda f: None)
            self.assertIn("磁碟", ctx.exception.user_message)
            self.assertEqual(os.listdir(self.dest), [])

    def test_download_cancel_mid_download_cleans_up(self):
        payload = b"c" * 100
        cancel = threading.Event()
        streams, read_up_to = [], []

        class CancellingStream(Stream):
            def read(self, n=-1):
                if self.tell() >= 30:
                    cancel.set()  # the user presses 取消 while chunks are arriving
                data = super().read(n)
                read_up_to.append(self.tell())
                return data

        def open_stream(url, timeout):
            streams.append(CancellingStream(payload))
            return streams[-1]

        with self.assertLogs(updater.log, "INFO"):
            with self.assertRaises(updater.UpdateError) as ctx:
                updater.download(asset_for(payload), self.dest, open_stream, lambda f: None, chunk=10, cancel=cancel)
        self.assertEqual(ctx.exception.user_message, "已取消更新")
        self.assertEqual(updater.MSG_CANCELLED, "已取消更新")
        self.assertEqual(os.listdir(self.dest), [])   # the partial file is deleted
        self.assertTrue(streams[0].closed)
        self.assertEqual(read_up_to[-1], 40)   # stopped at the next chunk boundary, not read to the end

    def test_download_cancelled_before_start(self):
        cancel = threading.Event()
        cancel.set()
        with self.assertLogs(updater.log, "INFO"):
            with self.assertRaises(updater.UpdateError) as ctx:
                updater.download(asset_for(b"x" * 10), self.dest, must_not_call, lambda f: None, cancel=cancel)
        self.assertEqual(ctx.exception.user_message, updater.MSG_CANCELLED)
        self.assertEqual(os.listdir(self.dest), [])

    def test_download_not_cancelled_completes(self):
        payload = b"k" * 50
        path = updater.download(asset_for(payload), self.dest, lambda url, timeout: Stream(payload), lambda f: None,
                                chunk=10, cancel=threading.Event())
        self.assertEqual(path.read_bytes(), payload)

    def test_download_rejects_bad_asset(self):
        payload = b"z" * 10
        good = asset_for(payload)
        for bad in [good._replace(url="http://github.com/x"), good._replace(size=0),
                    good._replace(size=updater.MAX_SIZE + 1), good._replace(name="../escape")]:
            with self.subTest(bad=bad):
                with self.assertRaises(updater.UpdateError):
                    updater.download(bad, self.dest, must_not_call, lambda f: None)
        self.assertEqual(os.listdir(self.dest), [])


def make_app_zip(path, members):
    """members: list of (name, data, mode) - mode may include stat.S_IFLNK for symlinks (data = target)."""
    with zipfile.ZipFile(path, "w") as zf:
        for name, data, mode in members:
            info = zipfile.ZipInfo(name)
            info.external_attr = (mode & 0xFFFF) << 16
            zf.writestr(info, data)


APP = "PixelBoard-Studio.app"
GOOD_APP_MEMBERS = [
    (f"{APP}/", b"", stat.S_IFDIR | 0o755),
    (f"{APP}/Contents/MacOS/PixelBoard-Studio", b"new binary", stat.S_IFREG | 0o755),
    (f"{APP}/Contents/Info.plist", b"<plist/>", stat.S_IFREG | 0o644),
    (f"{APP}/Contents/Frameworks/link", b"../MacOS/PixelBoard-Studio", stat.S_IFLNK | 0o777),
]


class InstallTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.install_dir = self.root / "install"
        self.install_dir.mkdir()
        self.dl_dir = self.root / "download"
        self.dl_dir.mkdir()

    def tearDown(self):
        os.chmod(self.install_dir, 0o755)
        self._tmp.cleanup()

    # --- Windows ---
    def _windows_setup(self):
        exe = self.install_dir / "PixelBoard-Studio.exe"
        exe.write_bytes(b"old exe")
        new = self.dl_dir / "PixelBoard-Studio-1.2.58-windows-x64.exe"
        new.write_bytes(b"new exe")
        return exe, new

    def test_install_windows_swaps_and_spawns(self):
        exe, new = self._windows_setup()
        spawn = Recorder()
        updater.install(new, "windows-x64", exe, spawn=spawn, open_url=must_not_call)
        self.assertEqual(exe.read_bytes(), b"new exe")
        self.assertEqual(exe.with_suffix(".exe.old").read_bytes(), b"old exe")
        self.assertFalse(new.exists())
        self.assertEqual(spawn.calls, [([str(exe)],)])

    def test_install_windows_rollback_on_failure(self):
        with self.subTest("spawn fails"):
            exe, new = self._windows_setup()
            with self.assertRaises(updater.UpdateError) as ctx:
                updater.install(new, "windows-x64", exe, spawn=Recorder(OSError("cannot start")),
                                open_url=must_not_call)
            self.assertTrue(ctx.exception.user_message)
            self.assertEqual(exe.read_bytes(), b"old exe")
            self.assertFalse(exe.with_suffix(".exe.old").exists())
        with self.subTest("move fails"):
            exe, new = self._windows_setup()
            spawn = Recorder()
            with mock.patch.object(updater.shutil, "move", side_effect=OSError("disk full")):
                with self.assertRaises(updater.UpdateError):
                    updater.install(new, "windows-x64", exe, spawn=spawn, open_url=must_not_call)
            self.assertEqual(exe.read_bytes(), b"old exe")
            self.assertFalse(exe.with_suffix(".exe.old").exists())
            self.assertEqual(spawn.calls, [])

    def test_install_windows_replaces_stale_old(self):
        exe, new = self._windows_setup()
        exe.with_suffix(".exe.old").write_bytes(b"ancient")
        updater.install(new, "windows-x64", exe, spawn=Recorder(), open_url=must_not_call)
        self.assertEqual(exe.with_suffix(".exe.old").read_bytes(), b"old exe")

    # --- Linux AppImage ---
    @unittest.skipUnless(POSIX, "POSIX file modes")
    def test_install_appimage_replaces_mode_755(self):
        appimage = self.install_dir / "PixelBoard-Studio.AppImage"
        appimage.write_bytes(b"old appimage")
        os.chmod(appimage, 0o755)
        new = self.dl_dir / "PixelBoard-Studio-1.2.58-linux-x86_64.AppImage"
        new.write_bytes(b"new appimage")
        os.chmod(new, 0o600)
        spawn = Recorder()
        updater.install(new, "linux-x86_64", appimage, spawn=spawn, open_url=must_not_call)
        self.assertEqual(appimage.read_bytes(), b"new appimage")
        self.assertEqual(stat.S_IMODE(appimage.stat().st_mode), 0o755)
        self.assertEqual(os.listdir(self.install_dir), [appimage.name])
        self.assertEqual(spawn.calls, [([str(appimage)],)])

    @unittest.skipUnless(POSIX, "POSIX file modes")
    def test_install_appimage_copy_failure_keeps_old(self):
        appimage = self.install_dir / "PixelBoard-Studio.AppImage"
        appimage.write_bytes(b"old appimage")
        new = self.dl_dir / "new.AppImage"
        new.write_bytes(b"new appimage")
        spawn = Recorder()
        with mock.patch.object(updater.shutil, "copyfileobj", side_effect=OSError("disk full")):
            with self.assertRaises(updater.UpdateError):
                updater.install(new, "linux-x86_64", appimage, spawn=spawn, open_url=must_not_call)
        self.assertEqual(appimage.read_bytes(), b"old appimage")
        self.assertEqual(os.listdir(self.install_dir), [appimage.name])
        self.assertEqual(spawn.calls, [])

    # --- macOS ---
    def _macos_setup(self, members):
        app = self.install_dir / APP
        (app / "Contents" / "MacOS").mkdir(parents=True)
        (app / "Contents" / "MacOS" / "PixelBoard-Studio").write_bytes(b"old binary")
        new_zip = self.dl_dir / "PixelBoard-Studio-1.2.58-macos-arm64.zip"
        make_app_zip(new_zip, members)
        return app, new_zip

    def test_install_macos_writes_helper(self):
        app, new_zip = self._macos_setup(GOOD_APP_MEMBERS)
        spawn = Recorder()
        updater.install(new_zip, "macos-arm64", app, spawn=spawn, open_url=must_not_call, pid=4242)
        self.assertEqual(len(spawn.calls), 1)
        (argv,) = spawn.calls[0]
        self.assertIsInstance(argv, list)
        self.assertEqual(len(argv), 2)
        self.assertEqual(argv[0], "/bin/sh")
        helper = Path(argv[1])
        text = helper.read_text(encoding="utf-8")
        self.assertIn("kill -0 4242", text)
        self.assertIn("sleep 0.2", text)
        self.assertIn('"$MV" "$APP" "$OLD"', text)
        self.assertIn('"$OPEN" "$APP"', text)
        self.assertIn(shlex.quote(str(app)), text)
        self.assertIn(shlex.quote(str(app) + ".old"), text)
        # new bundle is staged next to the old one; the old one is untouched until the helper runs
        staged = [p for p in self.install_dir.iterdir() if p.name != APP]
        self.assertEqual(len(staged), 1)
        new_app = staged[0] / APP
        self.assertEqual((new_app / "Contents/MacOS/PixelBoard-Studio").read_bytes(), b"new binary")
        self.assertIn(shlex.quote(str(new_app)), text)
        self.assertEqual((app / "Contents/MacOS/PixelBoard-Studio").read_bytes(), b"old binary")
        if POSIX:
            self.assertTrue(os.access(new_app / "Contents/MacOS/PixelBoard-Studio", os.X_OK))
            self.assertTrue((new_app / "Contents/Frameworks/link").is_symlink())

    def test_install_macos_rejects_bad_zip(self):
        f = stat.S_IFREG | 0o644
        cases = {
            "zip slip": GOOD_APP_MEMBERS + [("../evil", b"x", f)],
            "nested slip": GOOD_APP_MEMBERS + [(f"{APP}/../../evil", b"x", f)],
            "absolute": GOOD_APP_MEMBERS + [("/tmp/evil", b"x", f)],
            "backslash": GOOD_APP_MEMBERS + [(f"{APP}\\..\\evil", b"x", f)],
            "two apps": GOOD_APP_MEMBERS + [("Other.app/Contents/x", b"x", f)],
            "extra top-level file": GOOD_APP_MEMBERS + [("readme.txt", b"x", f)],
            "no app": [("PixelBoard-Studio/Contents/x", b"x", f)],
            "symlink escapes": GOOD_APP_MEMBERS + [(f"{APP}/Contents/esc", b"../../../etc", stat.S_IFLNK | 0o777)],
            "absolute symlink": GOOD_APP_MEMBERS + [(f"{APP}/Contents/abs", b"/etc", stat.S_IFLNK | 0o777)],
            "member under symlink": GOOD_APP_MEMBERS + [(f"{APP}/Contents/Frameworks/link/x", b"x", f)],
        }
        for label, members in cases.items():
            with self.subTest(label):
                app, new_zip = self._macos_setup(members)
                spawn = Recorder()
                with self.assertRaises(updater.UpdateError) as ctx:
                    updater.install(new_zip, "macos-arm64", app, spawn=spawn, open_url=must_not_call, pid=1)
                self.assertIn("下載的檔案不完整或已損壞", ctx.exception.user_message)
                self.assertEqual(spawn.calls, [])
                self.assertEqual(os.listdir(self.install_dir), [APP])
                self.assertFalse((self.root / "evil").exists())
                shutil.rmtree(app)

    def test_install_macos_not_a_zip(self):
        app, new_zip = self._macos_setup(GOOD_APP_MEMBERS)
        new_zip.write_bytes(b"this is not a zip")
        with self.assertRaises(updater.UpdateError) as ctx:
            updater.install(new_zip, "macos-arm64", app, spawn=must_not_call, open_url=must_not_call, pid=1)
        self.assertIn("下載的檔案不完整或已損壞", ctx.exception.user_message)
        self.assertEqual(os.listdir(self.install_dir), [APP])

    def _helper_setup(self, pid, open_ok=True, mv_fail_calls=(), wait_steps=updater.HELPER_WAIT_STEPS):
        """Writes a helper for a fake bundle pair; fake `open` records what it opened, fake `mv` fails on
        the given call numbers (1 = APP -> OLD, 2 = NEW -> APP, 3 = restore OLD -> APP)."""
        work = Path(tempfile.mkdtemp(dir=self.root))
        h = type("Helper", (), {})()
        h.app = work / "My App's.app"
        (h.app / "Contents").mkdir(parents=True)
        (h.app / "Contents" / "v").write_text("old")
        h.old = Path(str(h.app) + ".old")
        h.staging = work / ".staging"
        h.new_app = h.staging / "PixelBoard-Studio.app"
        (h.new_app / "Contents").mkdir(parents=True)
        (h.new_app / "Contents" / "v").write_text("new")
        h.opened = work / "opened.txt"
        h.log = work / "logs dir" / "update-helper.log"
        fake_open = work / "fake open.sh"
        fake_open.write_text(f'#!/bin/sh\ncat "$1/Contents/v" >> {shlex.quote(str(h.opened))}\n'
                             f'exit {0 if open_ok else 1}\n')
        os.chmod(fake_open, 0o755)
        counter = work / "mv-count"
        fails = " ".join(str(n) for n in mv_fail_calls) or "none"
        fake_mv = work / "fake mv.sh"
        fake_mv.write_text(f"""#!/bin/sh
n=$(($(cat {shlex.quote(str(counter))} 2>/dev/null || echo 0) + 1))
echo "$n" > {shlex.quote(str(counter))}
for f in {fails}; do
  if [ "$f" = "$n" ]; then echo "fake mv: call $n fails" >&2; exit 1; fi
done
exec /bin/mv "$@"
""")
        os.chmod(fake_mv, 0o755)
        h.helper = h.staging / "update.sh"
        h.helper.write_text(updater._macos_helper_script(h.app, h.new_app, h.staging, pid, open_cmd=str(fake_open),
                                                         mv_cmd=str(fake_mv), log_file=h.log,
                                                         wait_steps=wait_steps))
        return h

    def _run_helper(self, h):
        return subprocess.run(["/bin/sh", str(h.helper)], timeout=60, capture_output=True)

    def _dead_pid(self):
        proc = subprocess.Popen([sys.executable, "-c", "pass"])
        proc.wait()
        return proc.pid

    @unittest.skipUnless(POSIX and os.path.exists("/bin/sh"), "needs /bin/sh")
    def test_macos_helper_script_runs(self):
        dead_pid = self._dead_pid()
        for open_ok in (True, False):
            with self.subTest(open_ok=open_ok):
                h = self._helper_setup(dead_pid, open_ok=open_ok)
                result = self._run_helper(h)
                self.assertFalse(h.staging.exists())
                self.assertFalse(h.old.exists())
                self.assertTrue(h.log.exists())  # output goes to the log, its folder is created
                if open_ok:
                    self.assertEqual(result.returncode, 0, h.log.read_text())
                    self.assertEqual((h.app / "Contents" / "v").read_text(), "new")
                    self.assertEqual(h.opened.read_text(), "new")
                else:
                    self.assertNotEqual(result.returncode, 0)
                    self.assertEqual((h.app / "Contents" / "v").read_text(), "old")
                    self.assertEqual(h.opened.read_text(), "newold")

    @unittest.skipUnless(POSIX and os.path.exists("/bin/sh"), "needs /bin/sh")
    def test_macos_helper_pid_timeout_leaves_old_app(self):
        proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
        try:
            h = self._helper_setup(proc.pid, wait_steps=1)
            result = self._run_helper(h)
        finally:
            proc.kill()
            proc.wait()
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual((h.app / "Contents" / "v").read_text(), "old")  # untouched: it is still running
        self.assertFalse(h.old.exists())
        self.assertFalse(h.staging.exists())
        self.assertFalse(h.opened.exists())  # the old app is still open; nothing is started
        self.assertIn(f"pid {proc.pid} still running", h.log.read_text())

    @unittest.skipUnless(POSIX and os.path.exists("/bin/sh"), "needs /bin/sh")
    def test_macos_helper_mv_failures_reopen_old_app(self):
        dead_pid = self._dead_pid()
        for fail in ((1,), (2,)):
            with self.subTest(mv_fail_calls=fail):
                h = self._helper_setup(dead_pid, mv_fail_calls=fail)
                result = self._run_helper(h)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual((h.app / "Contents" / "v").read_text(), "old")
                self.assertEqual(h.opened.read_text(), "old")  # the old app is opened again
                self.assertFalse(h.old.exists())
                self.assertFalse(h.staging.exists())
                self.assertIn("fake mv: call", h.log.read_text())  # mv's stderr is logged too

    @unittest.skipUnless(POSIX and os.path.exists("/bin/sh"), "needs /bin/sh")
    def test_macos_helper_failed_restore_opens_remaining_app(self):
        h = self._helper_setup(self._dead_pid(), mv_fail_calls=(2, 3))
        result = self._run_helper(h)
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(h.app.exists())
        self.assertEqual((h.old / "Contents" / "v").read_text(), "old")  # old version kept, not deleted
        self.assertEqual(h.opened.read_text(), "old")
        self.assertIn("restoring", h.log.read_text())
        self.assertIn("failed", h.log.read_text())

    def test_macos_helper_validates_arguments(self):
        app, new = self.root / "A.app", self.root / "s" / "A.app"
        for kwargs in ({"pid": 0}, {"pid": True}, {"pid": 5, "wait_steps": 0}, {"pid": 5, "wait_steps": "3"}):
            with self.subTest(kwargs=kwargs):
                with self.assertRaises(ValueError):
                    updater._macos_helper_script(app, new, new.parent, **kwargs)

    def test_macos_helper_default_log_file(self):
        text = updater._macos_helper_script(self.root / "A.app", self.root / "s" / "A.app", self.root / "s", 7)
        expected = Path.home() / "Library" / "Logs" / "PixelBoard-Studio" / "update-helper.log"
        self.assertIn(shlex.quote(str(expected)), text)
        self.assertIn(f"-gt {updater.HELPER_WAIT_STEPS} ]", text)

    # --- permissions / dispatch ---
    def test_install_readonly_opens_release_page(self):
        exe, new = self._windows_setup()
        spawn, open_url = Recorder(), Recorder()
        if POSIX:
            if hasattr(os, "geteuid") and os.geteuid() == 0:
                self.skipTest("root ignores directory permissions")
            os.chmod(self.install_dir, 0o555)
            ctx_patch = contextlib.nullcontext()
        else:
            ctx_patch = mock.patch.object(updater.os, "access", return_value=False)
        with ctx_patch:
            with self.assertRaises(updater.UpdateError) as ctx:
                updater.install(new, "windows-x64", exe, spawn=spawn, open_url=open_url)
        self.assertIn("沒有權限更新", ctx.exception.user_message)
        self.assertEqual(open_url.calls, [(updater.RELEASES_URL,)])
        self.assertEqual(updater.RELEASES_URL, "https://github.com/alianlbj23/pixel_matrix/releases/latest")
        self.assertEqual(spawn.calls, [])
        self.assertEqual(exe.read_bytes(), b"old exe")
        self.assertTrue(new.exists())

    def test_install_readonly_open_url_fails(self):
        exe, new = self._windows_setup()
        with mock.patch.object(updater, "_dir_writable", return_value=False):
            with self.assertLogs(updater.log, "WARNING"):
                with self.assertRaises(updater.UpdateError) as ctx:
                    updater.install(new, "windows-x64", exe, spawn=must_not_call,
                                    open_url=Recorder(OSError("no browser")))
        self.assertIn("沒有權限更新", ctx.exception.user_message)
        self.assertIn(updater.RELEASES_URL, ctx.exception.user_message)

    def test_install_unknown_key(self):
        exe, new = self._windows_setup()
        with self.assertRaises(updater.UpdateError):
            updater.install(new, "amiga-68k", exe, spawn=must_not_call, open_url=must_not_call)
        self.assertEqual(exe.read_bytes(), b"old exe")

    def test_cleanup_old(self):
        exe = self.install_dir / "PixelBoard-Studio.exe"
        exe.write_bytes(b"current")
        old = self.install_dir / "PixelBoard-Studio.exe.old"
        old.write_bytes(b"old")
        updater.cleanup_old(exe)
        self.assertFalse(old.exists())
        self.assertTrue(exe.exists())

        app = self.install_dir / APP
        app.mkdir()
        old_app = self.install_dir / (APP + ".old")
        (old_app / "Contents").mkdir(parents=True)
        (old_app / "Contents" / "x").write_bytes(b"x")
        updater.cleanup_old(app)
        self.assertFalse(old_app.exists())
        self.assertTrue(app.exists())

        updater.cleanup_old(exe)  # nothing left: no error
        updater.cleanup_old(None)

        old.write_bytes(b"locked")
        with mock.patch.object(updater.os, "unlink", side_effect=PermissionError("in use")):
            with self.assertLogs(updater.log, "WARNING"):
                updater.cleanup_old(exe)
        self.assertTrue(old.exists())


# Self-signed test CA (certificate only, no key): loaded as a fallback CA bundle in SslContextTests.
TEST_CA_PEM = """-----BEGIN CERTIFICATE-----
MIIBkjCCATegAwIBAgIUQnoLckLinW09uRY6/80E6cEb5xgwCgYIKoZIzj0EAwIw
HTEbMBkGA1UEAwwSUGl4ZWxCb2FyZCBUZXN0IENBMCAXDTI2MTEyNTAyMjIzMloY
DzIxMjYxMTAxMDIyMjMyWjAdMRswGQYDVQQDDBJQaXhlbEJvYXJkIFRlc3QgQ0Ew
WTATBgcqhkjOPQIBBggqhkjOPQMBBwNCAATc0Df1mGNJbdQr6jKfTojCtDf5njmC
lKCfOAhUEyJfyVjaWkqSKpPkQAKCC3jzCuuPRSu0O4sl4ThkkDfb+6+ho1MwUTAd
BgNVHQ4EFgQU3SLKcwDFmlhH1TObBKAyPiqX2ZMwHwYDVR0jBBgwFoAU3SLKcwDF
mlhH1TObBKAyPiqX2ZMwDwYDVR0TAQH/BAUwAwEB/zAKBggqhkjOPQQDAgNJADBG
AiEA5Q37DNyublVu/NN9zOlQ67+4b1ROehkiAWgxi7sAdzQCIQCWTEQbm+f7m+DV
qpK4OxX5duwVLFrADj+qd0wl47fOVQ==
-----END CERTIFICATE-----
"""


class FakeVerifyPaths(NamedTuple):
    cafile: object
    capath: object


class SslContextTests(unittest.TestCase):
    """Frozen apps carry the build machine's OpenSSL paths; missing ones fall back to the system bundle."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)
        self.ca = self.dir / "cert.pem"
        self.ca.write_text(TEST_CA_PEM, encoding="ascii")
        self.missing = self.dir / "missing"

    def tearDown(self):
        self._tmp.cleanup()

    @staticmethod
    def _has_test_ca(ctx):
        return any(("commonName", "PixelBoard Test CA") in rdn
                   for cert in ctx.get_ca_certs() for rdn in cert.get("subject", ()))

    def test_fallback_bundle_loaded_when_default_paths_missing(self):
        paths = FakeVerifyPaths(str(self.missing / "cert.pem"), str(self.missing / "certs"))
        for system in ("darwin", "linux"):
            with self.subTest(system=system), self.assertLogs(updater.log, "INFO") as logs:
                ctx = updater._ssl_context(verify_paths=lambda: paths, system=system,
                                           candidates=(str(self.missing / "a.pem"), str(self.ca)))
            self.assertTrue(self._has_test_ca(ctx))
            self.assertIn(str(self.ca), "\n".join(logs.output))
            self.assertEqual(ctx.verify_mode, updater.ssl.CERT_REQUIRED)
            self.assertTrue(ctx.check_hostname)

    def test_default_paths_present_no_fallback(self):
        for paths in (FakeVerifyPaths(str(self.ca), None), FakeVerifyPaths(None, str(self.dir))):
            with self.subTest(paths=paths):
                ctx = updater._ssl_context(verify_paths=lambda: paths, system="linux", candidates=(str(self.ca),))
                self.assertFalse(self._has_test_ca(ctx))

    def test_no_bundle_found_logs_error(self):
        paths = FakeVerifyPaths(None, None)
        tried = (str(self.missing / "a.pem"), str(self.missing / "b.pem"))
        with self.assertLogs(updater.log, "ERROR") as logs:
            ctx = updater._ssl_context(verify_paths=lambda: paths, system="linux", candidates=tried)
        self.assertIsInstance(ctx, updater.ssl.SSLContext)
        for path in tried:
            self.assertIn(path, "\n".join(logs.output))

    def test_broken_bundle_skipped(self):
        bad = self.dir / "bad.pem"
        bad.write_text("not a certificate", encoding="ascii")
        with self.assertLogs(updater.log, "WARNING") as logs:
            ctx = updater._ssl_context(verify_paths=lambda: FakeVerifyPaths(None, None), system="linux",
                                       candidates=(str(bad), str(self.ca)))
        self.assertTrue(self._has_test_ca(ctx))
        self.assertIn(str(bad), "\n".join(logs.output))

    def test_platform_candidates(self):
        self.assertEqual(updater.ca_bundle_candidates("darwin"), ("/etc/ssl/cert.pem",))
        self.assertEqual(updater.ca_bundle_candidates("linux"), (
            "/etc/ssl/certs/ca-certificates.crt", "/etc/pki/tls/certs/ca-bundle.crt",
            "/etc/ssl/ca-bundle.pem", "/etc/ssl/cert.pem"))
        self.assertEqual(updater.ca_bundle_candidates("win32"), ())  # Windows uses the system store

    def test_open_uses_fallback_context(self):
        sentinel = updater.ssl.create_default_context()
        with mock.patch.object(updater, "_ssl_context", return_value=sentinel) as made, \
                mock.patch.object(updater.urllib.request, "HTTPSHandler") as handler, \
                mock.patch.object(updater.urllib.request, "build_opener") as build:
            updater._open("https://api.github.com/x", 5)
        made.assert_called_once_with()
        handler.assert_called_once_with(context=sentinel)
        build.return_value.open.assert_called_once()


class PlumbingTests(unittest.TestCase):
    def test_current_executable(self):
        self.assertIsNone(updater.current_executable(frozen=False))
        self.assertEqual(updater.current_executable(frozen=True, system="win32", executable=r"C:\A\P.exe", env={}),
                         Path(r"C:\A\P.exe"))
        self.assertEqual(updater.current_executable(frozen=True, system="linux", executable="/tmp/.mount/x",
                                                    env={"APPIMAGE": "/home/u/P.AppImage"}),
                         Path("/home/u/P.AppImage"))
        self.assertIsNone(updater.current_executable(frozen=True, system="linux", executable="/x", env={}))
        self.assertEqual(updater.current_executable(
            frozen=True, system="darwin", executable="/Applications/P S.app/Contents/MacOS/P", env={}),
            Path("/Applications/P S.app"))
        self.assertIsNone(updater.current_executable(frozen=True, system="darwin", executable="/usr/bin/x", env={}))

    def test_http_rejects_non_https(self):
        with mock.patch.object(updater.urllib.request, "build_opener", side_effect=AssertionError("opened")):
            for url in ["http://api.github.com/x", "file:///etc/passwd", "ftp://x/y", "https:///nohost", 42]:
                with self.subTest(url=url):
                    with self.assertRaises(updater.UpdateError):
                        updater.http_get(url, 5)
                    with self.assertRaises(updater.UpdateError):
                        updater.open_stream(url, 5)

    def test_redirect_handler_https_only(self):
        handler = updater._HttpsOnlyRedirectHandler()
        req = updater.urllib.request.Request("https://github.com/a")
        with self.assertRaises(urllib.error.HTTPError):
            handler.redirect_request(req, None, 302, "Found", {}, "http://objects.githubusercontent.com/a")
        new = handler.redirect_request(req, None, 302, "Found", {}, "https://objects.githubusercontent.com/a")
        self.assertEqual(new.full_url, "https://objects.githubusercontent.com/a")

    def test_request_headers(self):
        req = updater._request("https://api.github.com/repos/x/y/releases/latest")
        self.assertTrue(req.get_header("User-agent").startswith("PixelBoard-Studio/"))
        self.assertEqual(req.get_header("Accept"), "application/vnd.github+json")
        self.assertIsNone(updater._request(PREFIX + "latest.json").get_header("Accept"))

    def test_spawn_detached(self):
        with mock.patch.object(updater.subprocess, "Popen") as popen:
            updater.spawn_detached(["/x/app"])
        args, kwargs = popen.call_args
        self.assertEqual(args[0], ["/x/app"])
        self.assertEqual(kwargs["env"]["PYINSTALLER_RESET_ENVIRONMENT"], "1")
        self.assertNotIn("shell", kwargs)
        if os.name == "nt":
            self.assertTrue(kwargs["creationflags"] & subprocess.DETACHED_PROCESS)
        else:
            self.assertTrue(kwargs["start_new_session"])
        with self.assertRaises(TypeError):
            updater.spawn_detached("/x/app --flag")


if __name__ == "__main__":
    unittest.main()
