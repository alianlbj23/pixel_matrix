"""pywebview window and the Python API the HTML UI calls (window.pywebview.api.*)."""
import json
import logging
import pathlib
import threading

from . import firmware
from .board import BoardError, find_board_ports, list_system_ports
from .flasher import Flasher, to_board_error

log = logging.getLogger(__name__)

UI_DIR = pathlib.Path(__file__).resolve().parent / "ui"
MSG_NO_BOARD = "找不到板子。請用可傳資料的 USB 線接到板子的 Type-C 資料口（不是 POWER 口）"
MSG_BUSY = "正在燒錄中，請稍候"


class StudioApi:
    """Every public method returns a JSON-serialisable dict and never raises to JS."""

    def __init__(self):
        self._window = None  # set by run(); underscore keeps it out of the JS bridge
        self._lock = threading.Lock()

    def _progress(self, stage: str, fraction: float) -> None:
        if self._window is not None:
            self._window.evaluate_js(f"window.studio.onProgress({json.dumps(stage)}, {fraction:.3f})")

    def find_board(self) -> dict:
        try:
            ports = find_board_ports(list_system_ports())
        except Exception as exc:  # noqa: BLE001
            log.exception("listing serial ports failed")
            return {"ports": [], "message": to_board_error(exc).user_message}
        if not ports:
            return {"ports": [], "message": MSG_NO_BOARD}
        if len(ports) == 1:
            return {"ports": ports, "message": f"找到板子：{ports[0]}"}
        return {"ports": ports, "message": f"找到 {len(ports)} 塊板子，請選擇要燒錄的那一塊"}

    def flash_firmware(self, port: str = None) -> dict:
        if not self._lock.acquire(blocking=False):
            return {"ok": False, "message": MSG_BUSY}
        flasher = None
        try:
            if not port:
                ports = find_board_ports(list_system_ports())
                if not ports:
                    return {"ok": False, "message": MSG_NO_BOARD}
                port = ports[0]
            images = firmware.load_images()
            flasher = Flasher(port, self._progress)
            flasher.connect()
            flasher.write([(firmware.IMAGE_OFFSETS[name], data) for name, data in images.items()])
            flasher.hard_reset()
            log.info("firmware flashed to %s", port)
            return {"ok": True, "message": "燒錄完成，板子已重新啟動"}
        except BoardError as exc:
            return {"ok": False, "message": exc.user_message}
        except Exception as exc:  # noqa: BLE001
            log.exception("flash_firmware failed")
            return {"ok": False, "message": to_board_error(exc).user_message}
        finally:
            if flasher is not None:
                flasher.close()
            self._lock.release()


def run() -> None:
    import webview  # imported here so the API can be tested without a GUI toolkit

    api = StudioApi()
    window = webview.create_window("PixelBoard Studio", url=str(UI_DIR / "index.html"), js_api=api,
                                   width=1000, height=720, min_size=(800, 600))
    api._window = window
    webview.start(gui="edgechromium")
