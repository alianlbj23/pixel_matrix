"""Entry point of PixelBoard-Studio.exe."""
import logging
import os
import pathlib
import subprocess
import sys

from pixelboard import paths

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


def main() -> int:
    logging.basicConfig(filename=_log_path(), level=logging.INFO, encoding="utf-8",
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    log = logging.getLogger("studio")
    log.info("starting PixelBoard Studio")
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
