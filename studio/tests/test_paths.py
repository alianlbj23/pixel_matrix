import unittest
from pathlib import Path

from pixelboard import paths
from pixelboard.app import gui_for

HOME = Path("/h")


class PathsTest(unittest.TestCase):
    def test_paths_windows(self):
        d = paths.data_dir("win32", {"LOCALAPPDATA": "C:/L"}, HOME)
        self.assertEqual(d, Path("C:/L") / "PixelBoard-Studio")
        self.assertEqual(paths.data_dir("win32", {}, HOME), HOME / "AppData/Local/PixelBoard-Studio")
        self.assertEqual(paths.log_dir("win32", {}, HOME), paths.data_dir("win32", {}, HOME))

    def test_paths_macos(self):
        self.assertEqual(paths.data_dir("darwin", {}, HOME), HOME / "Library/Application Support/PixelBoard-Studio")
        self.assertEqual(paths.log_dir("darwin", {}, HOME), HOME / "Library/Logs/PixelBoard-Studio")
        self.assertEqual(paths.layout_path("darwin", {}, HOME),
                         HOME / "Library/Application Support/PixelBoard-Studio/layout.json")

    def test_paths_linux_xdg(self):
        self.assertEqual(paths.data_dir("linux", {"XDG_DATA_HOME": "/x"}, HOME), Path("/x/PixelBoard-Studio"))
        self.assertEqual(paths.log_dir("linux", {"XDG_DATA_HOME": "/x"}, HOME), Path("/x/PixelBoard-Studio"))

    def test_paths_linux_default(self):
        self.assertEqual(paths.data_dir("linux", {}, HOME), HOME / ".local/share/PixelBoard-Studio")
        self.assertEqual(paths.layout_path("linux", {}, HOME), HOME / ".local/share/PixelBoard-Studio/layout.json")

    def test_gui_for(self):
        self.assertEqual(gui_for("win32"), "edgechromium")
        self.assertEqual(gui_for("darwin"), "cocoa")
        self.assertEqual(gui_for("linux"), "qt")
