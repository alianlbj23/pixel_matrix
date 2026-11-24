"""Entry point of PixelBoard-Studio.exe."""
import logging
import os
import pathlib
import sys

WEBVIEW2_MISSING = ("這台電腦缺少 Microsoft Edge WebView2，請到 "
                    "https://go.microsoft.com/fwlink/p/?LinkId=2124703 安裝後再開啟")


def _log_path() -> pathlib.Path:
    base = pathlib.Path(os.environ.get("LOCALAPPDATA") or pathlib.Path.home()) / "PixelBoard-Studio"
    base.mkdir(parents=True, exist_ok=True)
    return base / "studio.log"


def _message_box(text: str) -> None:
    if sys.platform == "win32":
        import ctypes

        ctypes.windll.user32.MessageBoxW(None, text, "PixelBoard Studio", 0x10)
    else:
        print(text, file=sys.stderr)


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
        if "webview2" in text or "edgechromium" in text or "edge" in text:
            _message_box(WEBVIEW2_MISSING)
        else:
            _message_box(f"PixelBoard Studio 無法啟動：{exc}\n\n詳細記錄：{_log_path()}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
