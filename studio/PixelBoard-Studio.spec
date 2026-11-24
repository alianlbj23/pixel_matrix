# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec for PixelBoard-Studio.exe (one file, no console). Built by build_exe.sh.
from PyInstaller.utils.hooks import collect_data_files, collect_submodules

datas = [("pixelboard/ui", "pixelboard/ui"), ("firmware", "firmware")]
datas += collect_data_files("esptool")          # stub flasher images for every chip
hiddenimports = collect_submodules("esptool")    # chip targets are imported dynamically

a = Analysis(["run_studio.py"], pathex=["."], datas=datas, hiddenimports=hiddenimports,
             excludes=["tkinter"], noarchive=False)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, a.binaries, a.datas, [], name="PixelBoard-Studio",
          console=False, upx=False, runtime_tmpdir=None)
