"""Entry point of PixelBoard Studio (Windows exe, macOS app, Linux AppImage).

``--version`` prints the app version and exits without opening a window or a log.
``--version-file <path>`` writes it to <path> instead (the Windows exe is windowed, so its stdout
may not reach a shell; CI reads the file).
"""
import logging
import os
import pathlib
import subprocess
import sys

from pixelboard import paths
from pixelboard.version import current_version

WEBVIEW2_MISSING = ("這台電腦缺少 Microsoft Edge WebView2，請到 "
                    "https://go.microsoft.com/fwlink/p/?LinkId=2124703 安裝後再開啟")


def _log_path() -> pathlib.Path:
    base = paths.log_dir()
    base.mkdir(parents=True, exist_ok=True)
    return base / "studio.log"


def _applescript_quote(text: str) -> str:
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _message_box(text: str) -> None:
    log = logging.getLogger("studio")
    try:
        if sys.platform == "win32":
            import ctypes

            ctypes.windll.user32.MessageBoxW(None, text, "PixelBoard Studio", 0x10)
        elif sys.platform == "darwin":
            script = f"display alert {_applescript_quote('PixelBoard Studio')} message {_applescript_quote(text)}"
            subprocess.run(["osascript", "-e", script], timeout=30, check=False)
        else:
            _linux_message_box(text)
    except Exception:  # noqa: BLE001 - the error path itself must never crash
        log.exception("could not show error dialog")
        print(text, file=sys.stderr)


def _linux_message_box(text: str) -> None:
    print(text, file=sys.stderr)
    if not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")):
        return
    try:
        from qtpy.QtWidgets import QApplication, QMessageBox
    except ImportError:
        logging.getLogger("studio").info("Qt not available, dialog skipped")
        return
    if QApplication.instance() is not None:
        return
    app = QApplication([])
    QMessageBox.critical(None, "PixelBoard Studio", text)
    del app


def _cleanup_old_update() -> None:
    """Removes the previous version an update left behind (<exe>.old); packaged builds only, never fatal."""
    log = logging.getLogger("studio")
    try:
        from pixelboard import updater

        exe = updater.current_executable()
        if exe is not None:
            updater.cleanup_old(exe)
    except Exception:  # noqa: BLE001 - a leftover file must not stop the app from starting
        log.exception("cleaning up the previous version failed")


def _write_version_file(args) -> int:
    """--version-file <path>: 0 written, 2 bad arguments, 1 write failed (reported on stderr, no log yet)."""
    if len(args) != 1 or not args[0].strip():
        print("usage: --version-file <path>", file=sys.stderr)
        return 2
    try:
        pathlib.Path(args[0]).write_text(current_version() + "\n", encoding="utf-8")
    except OSError as exc:
        print(f"--version-file: cannot write {args[0]}: {exc}", file=sys.stderr)
        return 1
    return 0


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if argv == ["--version"]:
        print(current_version())
        return 0
    if argv[:1] == ["--version-file"]:
        return _write_version_file(argv[1:])
    logging.basicConfig(filename=_log_path(), level=logging.INFO, encoding="utf-8",
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    log = logging.getLogger("studio")
    log.info("starting PixelBoard Studio %s", current_version())
    _cleanup_old_update()
    try:
        from pixelboard.app import run

        run()
    except Exception as exc:  # noqa: BLE001 - last line of defence, shown to the user
        log.exception("startup failed")
        text = str(exc).lower()
        if sys.platform == "win32" and ("webview2" in text or "edgechromium" in text or "edge" in text):
            _message_box(WEBVIEW2_MISSING)
        else:
            _message_box(f"PixelBoard Studio 無法啟動：{exc}\n\n詳細記錄：{_log_path()}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
