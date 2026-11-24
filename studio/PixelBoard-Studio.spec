# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec for PixelBoard Studio. Run from studio/ (or a staged copy of it) after placing the
# firmware images in firmware/ and, for releases, generating pixelboard/_version.py (write_version.py).
#   win32  : dist/PixelBoard-Studio.exe        (one file, no console; built locally by build_exe.sh)
#   darwin : dist/PixelBoard Studio.app        (onedir bundle; zipped by CI)
#   linux  : dist/PixelBoard-Studio/           (onedir; turned into an AppImage by packaging/build_appimage.sh)
# The icon is a PNG on every platform: PyInstaller converts it to .ico/.icns with Pillow (a pinned requirement).
import ast
import pathlib
import sys

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

HERE = pathlib.Path(SPECPATH)  # noqa: F821 - injected by PyInstaller; paths below do not depend on the cwd
NAME = "PixelBoard-Studio"
ICON = str(HERE / "packaging" / "icon.png")
VERSION_FILE = HERE / "pixelboard" / "_version.py"


def _app_version() -> str:
    """APP_VERSION from the generated pixelboard/_version.py (parsed, not imported), else "0.0.0"."""
    if not VERSION_FILE.is_file():
        return "0.0.0"
    for node in ast.parse(VERSION_FILE.read_text(encoding="utf-8")).body:
        if isinstance(node, ast.Assign) and any(getattr(t, "id", None) == "APP_VERSION" for t in node.targets):
            value = ast.literal_eval(node.value)
            if isinstance(value, str):
                return value
    raise SystemExit(f"{VERSION_FILE}: no APP_VERSION string")


APP_VERSION = _app_version()

datas = [("pixelboard/ui", "pixelboard/ui"), ("firmware", "firmware")]
datas += collect_data_files("esptool")          # stub flasher images for every chip
hiddenimports = collect_submodules("esptool")    # chip targets are imported dynamically
if VERSION_FILE.is_file():
    hiddenimports.append("pixelboard._version")  # imported lazily by pixelboard.version.current_version()

a = Analysis(["run_studio.py"], pathex=["."], datas=datas, hiddenimports=hiddenimports,
             excludes=["tkinter"], noarchive=False)
pyz = PYZ(a.pure)

if sys.platform == "win32":
    exe = EXE(pyz, a.scripts, a.binaries, a.datas, [], name=NAME, icon=ICON,
              console=False, upx=False, runtime_tmpdir=None)
else:
    exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name=NAME,
              console=False, upx=False)
    coll = COLLECT(exe, a.binaries, a.datas, name=NAME, upx=False)
    if sys.platform == "darwin":
        app = BUNDLE(coll, name="PixelBoard Studio.app", icon=ICON,
                     bundle_identifier="io.github.alianlbj23.pixelboard-studio",
                     version=APP_VERSION,
                     info_plist={
                         "CFBundleShortVersionString": APP_VERSION,
                         "LSMinimumSystemVersion": "11.0",
                         "NSPrincipalClass": "NSApplication",
                         "NSHighResolutionCapable": True,
                     })
