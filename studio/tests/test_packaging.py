import contextlib
import importlib.util
import io
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

from pixelboard import version

STUDIO = pathlib.Path(__file__).resolve().parent.parent
PACKAGING = STUDIO / "packaging"
WRITE_VERSION = PACKAGING / "write_version.py"
BUILD_APPIMAGE = PACKAGING / "build_appimage.sh"
DESKTOP = PACKAGING / "pixelboard-studio.desktop"
ICON = PACKAGING / "icon.png"
BASH = shutil.which("bash")


def _import_file(path: pathlib.Path):
    spec = importlib.util.spec_from_file_location("generated_version", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _desktop_entries() -> dict:
    entries = {}
    lines = DESKTOP.read_text(encoding="utf-8").splitlines()
    assert lines[0] == "[Desktop Entry]", lines[0]
    for line in lines[1:]:
        if line and not line.startswith("#"):
            key, _, value = line.partition("=")
            entries[key] = value
    return entries


class WriteVersionTests(unittest.TestCase):
    def _write(self, ver: str, out: pathlib.Path) -> subprocess.CompletedProcess:
        return subprocess.run([sys.executable, str(WRITE_VERSION), ver, str(out)],
                              capture_output=True, text=True, timeout=30)

    def test_release_version_is_importable(self):
        with tempfile.TemporaryDirectory() as d:
            out = pathlib.Path(d) / "_version.py"
            r = self._write("1.2.57", out)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual(_import_file(out).APP_VERSION, "1.2.57")

    def test_dev_version_is_importable(self):
        # build_exe.sh writes "<studio/VERSION>.0-dev" so a local build reports a dev version.
        with tempfile.TemporaryDirectory() as d:
            out = pathlib.Path(d) / "_version.py"
            r = self._write("1.2.0-dev", out)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual(_import_file(out).APP_VERSION, "1.2.0-dev")

    def test_other_suffixes_rejected(self):
        for bad in ["1.2.0-rc1", "1.2-dev", "1.2.0-dev-dev", "-dev"]:
            with self.subTest(bad=bad), tempfile.TemporaryDirectory() as d:
                out = pathlib.Path(d) / "_version.py"
                self.assertEqual(self._write(bad, out).returncode, 1)
                self.assertFalse(out.exists())


class DesktopFileTests(unittest.TestCase):
    def test_required_keys(self):
        entries = _desktop_entries()
        self.assertEqual(entries.get("Type"), "Application")
        self.assertEqual(entries.get("Exec"), "PixelBoard-Studio")
        self.assertEqual(entries.get("Name"), "PixelBoard Studio")
        self.assertTrue(entries.get("Icon"))
        self.assertTrue(entries.get("Categories", "").endswith(";"))

    def test_icon_is_256_png(self):
        from PIL import Image

        with Image.open(ICON) as im:
            self.assertEqual(im.format, "PNG")
            self.assertEqual(im.size, (256, 256))


class RunStudioVersionTests(unittest.TestCase):
    def test_version_flag_prints_version_and_exits(self):
        with tempfile.TemporaryDirectory() as home:
            env = dict(os.environ, HOME=home, XDG_DATA_HOME=home, LOCALAPPDATA=home)
            r = subprocess.run([sys.executable, str(STUDIO / "run_studio.py"), "--version"], cwd=str(STUDIO),
                               env=env, capture_output=True, text=True, timeout=60)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual(r.stdout.strip(), version.current_version())
            self.assertEqual(list(pathlib.Path(home).iterdir()), [], "--version must not create the log")

    def test_version_file_flag_writes_version_and_exits(self):
        with tempfile.TemporaryDirectory() as home, tempfile.TemporaryDirectory() as out_dir:
            out = pathlib.Path(out_dir) / "ver sion.txt"
            env = dict(os.environ, HOME=home, XDG_DATA_HOME=home, LOCALAPPDATA=home)
            r = subprocess.run([sys.executable, str(STUDIO / "run_studio.py"), "--version-file", str(out)],
                               cwd=str(STUDIO), env=env, capture_output=True, text=True, timeout=60)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual(out.read_text(encoding="utf-8"), version.current_version() + "\n")
            self.assertEqual(list(pathlib.Path(home).iterdir()), [], "--version-file must not create the log")

    def test_version_file_flag_errors(self):
        sys.path.insert(0, str(STUDIO))
        self.addCleanup(sys.path.remove, str(STUDIO))
        import run_studio

        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch.object(run_studio, "_log_path", side_effect=AssertionError("log opened")):
            for argv, code in ((["--version-file"], 2), (["--version-file", ""], 2),
                               (["--version-file", "a", "b"], 2),
                               (["--version-file", str(pathlib.Path(tmp) / "missing" / "v.txt")], 1),
                               (["--version-file", tmp], 1)):
                with self.subTest(argv=argv):
                    err = io.StringIO()
                    with contextlib.redirect_stderr(err):
                        self.assertEqual(run_studio.main(argv), code)
                    self.assertIn("--version-file", err.getvalue())


@unittest.skipUnless(BASH and sys.platform.startswith("linux"), "build_appimage.sh is a Linux bash script")
class BuildAppImageTests(unittest.TestCase):
    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)
        self.onedir = self.tmp / "PixelBoard-Studio"
        (self.onedir / "_internal").mkdir(parents=True)
        exe = self.onedir / "PixelBoard-Studio"
        exe.write_text("#!/bin/sh\necho fake\n")
        exe.chmod(0o755)
        (self.onedir / "_internal" / "lib.so").write_text("x")
        # Fake appimagetool: records the AppDir it was given and the env, then writes the output file.
        self.record = self.tmp / "record"
        self.tool = self.tmp / "appimagetool"
        self.tool.write_text('#!/bin/sh\nset -e\n'
                             f'echo "ARCH=$ARCH VERSION=$VERSION" > "{self.record}/env"\n'
                             f'echo "$@" > "{self.record}/args"\n'
                             'if [ "$1" = --runtime-file ]; then shift 2; fi\n'
                             f'cp -a "$1" "{self.record}/AppDir"\n'
                             'echo appimage > "$2"\n')
        self.tool.chmod(0o755)
        self.record.mkdir()
        self.out = self.tmp / "out" / "PixelBoard-Studio-1.2.57-linux-x86_64.AppImage"

    def _run(self, *args, runtime=None) -> subprocess.CompletedProcess:
        env = {k: v for k, v in os.environ.items() if k != "APPIMAGE_RUNTIME_FILE"}
        if runtime is not None:
            env["APPIMAGE_RUNTIME_FILE"] = str(runtime)
        return subprocess.run([BASH, str(BUILD_APPIMAGE), *map(str, args)], capture_output=True, text=True,
                              env=env, timeout=60)

    def test_missing_arguments_fail(self):
        for args in [(), (self.onedir,), (self.onedir, "1.2.57", self.out)]:
            with self.subTest(n=len(args)):
                r = self._run(*args)
                self.assertNotEqual(r.returncode, 0)
                self.assertIn("usage", r.stderr)

    def test_invalid_arguments_fail(self):
        cases = {
            "missing onedir": (self.tmp / "nope", "1.2.57", self.out, self.tool),
            "bad version": (self.onedir, "1.2", self.out, self.tool),
            "tool not executable": (self.onedir, "1.2.57", self.out, self.tmp / "nope-tool"),
        }
        for name, args in cases.items():
            with self.subTest(name):
                r = self._run(*args)
                self.assertNotEqual(r.returncode, 0)
                self.assertFalse(self.out.exists())

    def test_builds_appdir_layout(self):
        r = self._run(self.onedir, "1.2.57", self.out, self.tool)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.out.read_text(), "appimage\n")
        self.assertEqual((self.record / "env").read_text().strip(), "ARCH=x86_64 VERSION=1.2.57")
        appdir = self.record / "AppDir"
        self.assertTrue(os.access(appdir / "usr" / "bin" / "PixelBoard-Studio", os.X_OK))
        self.assertTrue((appdir / "usr" / "bin" / "_internal" / "lib.so").is_file())
        self.assertTrue(os.access(appdir / "AppRun", os.X_OK))
        self.assertIn('usr/bin/PixelBoard-Studio" "$@"', (appdir / "AppRun").read_text())
        self.assertTrue((appdir / "pixelboard-studio.desktop").is_file())
        icon_name = _desktop_entries()["Icon"]
        self.assertEqual((appdir / f"{icon_name}.png").read_bytes(), ICON.read_bytes())
        self.assertEqual((appdir / ".DirIcon").read_bytes(), ICON.read_bytes())
        self.assertNotIn("--runtime-file", (self.record / "args").read_text())

    def test_pinned_runtime_file_is_passed(self):
        runtime = self.tmp / "runtime-x86_64"
        runtime.write_bytes(b"runtime")
        r = self._run(self.onedir, "1.2.57", self.out, self.tool, runtime=runtime)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue((self.record / "args").read_text().startswith(f"--runtime-file {runtime} "))

    def test_missing_runtime_file_fails(self):
        r = self._run(self.onedir, "1.2.57", self.out, self.tool, runtime=self.tmp / "nope")
        self.assertNotEqual(r.returncode, 0)
        self.assertFalse(self.out.exists())


if __name__ == "__main__":
    unittest.main()
