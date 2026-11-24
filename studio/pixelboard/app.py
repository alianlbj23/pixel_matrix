"""pywebview window and the Python API the HTML UI calls (window.pywebview.api.*).

Every public StudioApi method returns a JSON-serialisable dict and never raises to JS:
failures come back as {"ok": False, "message": <plain-language Chinese text>}.
Flashing is two steps so the user can confirm what will be written:
prepare_flash() connects and plans, then confirm_flash() writes (or cancel_flash()).
"""
import base64
import json
import logging
import pathlib
import threading
from typing import Callable, Optional

from . import firmware, gifproc, pxa, pxg
from .board import BoardError, find_board_ports, list_system_ports, plan_flash, read_state
from .flasher import Flasher, to_board_error

log = logging.getLogger(__name__)

UI_DIR = pathlib.Path(__file__).resolve().parent / "ui"
MSG_NO_BOARD = "找不到板子。請用可傳資料的 USB 線接到板子的 Type-C 資料口（不是 POWER 口）"
MSG_BUSY = "正在燒錄中，請稍候"
MSG_NO_GIF = "請先加入一個 GIF"
MSG_NO_PENDING = "請重新按一次「燒錄」"
MSG_BAD_INDEX = "找不到這首音樂"
MSG_BAD_LAYOUT = "設定值不正確，請重新調整"
MAX_TRACK_BYTES = pxa.CAPACITY


def _fail(message: str) -> dict:
    return {"ok": False, "message": message}


def _layout_from(d: dict):
    """Validates the layout dict coming from JS. Returns (gifproc.Layout, brightness, swap)."""
    try:
        layout = gifproc.Layout(chain=int(d["chain"]), split=bool(d["split"]), offset_x=int(d["offset_x"]),
                                offset_y=int(d["offset_y"]), scale=float(d["scale"]))
        brightness, swap = int(d["brightness"]), bool(d["swap"])
    except (KeyError, TypeError, ValueError) as exc:
        raise gifproc.GifError(MSG_BAD_LAYOUT) from exc
    layout.validate()
    if not pxg.MIN_BRIGHTNESS <= brightness <= pxg.MAX_BRIGHTNESS:
        raise gifproc.GifError(f"亮度要在 {pxg.MIN_BRIGHTNESS}–{pxg.MAX_BRIGHTNESS} 之間")
    return layout, brightness, swap


class StudioApi:
    def __init__(self, list_ports: Callable = None, flasher_factory: Callable = None,
                 images_loader: Callable = None):
        self._window = None  # set by run(); underscore keeps it out of the JS bridge
        self._list_ports = list_ports or list_system_ports
        self._flasher_factory = flasher_factory or Flasher
        self._load_images = images_loader or firmware.load_images
        self._lock = threading.Lock()
        self._gif: Optional[gifproc.SourceGif] = None
        self._tracks: list = []   # dicts: name, data, rate, channels
        self._pending = None      # (flasher, plan) between prepare_flash and confirm_flash

    # ---- progress / UI bridge -------------------------------------------------
    def _progress(self, stage: str, fraction: float) -> None:
        if self._window is not None:
            self._window.evaluate_js(f"window.studio.onProgress({json.dumps(stage)}, {fraction:.3f})")

    def pick_files(self, kind: str) -> dict:
        """Opens a file dialog; kind is "gif" or "mp3". Returns {"ok", "paths"}."""
        if self._window is None:
            return _fail("視窗尚未就緒")
        import webview

        types = ("GIF 動畫 (*.gif)",) if kind == "gif" else ("MP3 音樂 (*.mp3)",)
        paths = self._window.create_file_dialog(webview.FileDialog.OPEN, allow_multiple=(kind == "mp3"),
                                                file_types=types)
        return {"ok": True, "paths": list(paths or [])}

    # ---- GIF ------------------------------------------------------------------
    def open_gif(self, path: str) -> dict:
        try:
            src = gifproc.load(path)
        except gifproc.GifError as exc:
            return _fail(exc.user_message)
        except Exception as exc:  # noqa: BLE001
            log.exception("open_gif failed")
            return _fail(f"無法開啟 GIF：{exc}")
        self._gif = src
        return {"ok": True, "name": src.name, "frames": len(src.frames)}

    def _render(self, layout_dict: dict):
        if self._gif is None:
            raise gifproc.GifError(MSG_NO_GIF)
        layout, brightness, swap = _layout_from(layout_dict)
        return gifproc.render(self._gif, layout), layout, brightness, swap

    def render_preview(self, layout: dict) -> dict:
        try:
            frames, lay, brightness, _ = self._render(layout)
        except gifproc.GifError as exc:
            return _fail(exc.user_message)
        except Exception as exc:  # noqa: BLE001
            log.exception("render_preview failed")
            return _fail(f"預覽失敗：{exc}")
        return {"ok": True, "width": gifproc.PANEL_W * lay.chain, "height": gifproc.PANEL_H,
                "brightness": brightness,
                "frames": [{"rgb_b64": base64.b64encode(rgb).decode("ascii"), "ms": ms} for rgb, ms in frames]}

    # ---- music ----------------------------------------------------------------
    def _music_state(self, rejected=None) -> dict:
        return {"ok": True,
                "tracks": [{"name": t["name"], "size": len(t["data"]), "rate": t["rate"], "channels": t["channels"]}
                           for t in self._tracks],
                "used": pxa.packed_size([len(t["data"]) for t in self._tracks]),
                "capacity": pxa.CAPACITY,
                "rejected": rejected or []}

    def add_tracks(self, paths: list) -> dict:
        rejected = []
        for p in paths or []:
            path = pathlib.Path(str(p))
            try:
                if path.stat().st_size > MAX_TRACK_BYTES:
                    raise pxa.PxaError("檔案太大，超過音樂容量")
                data = path.read_bytes()
                info = pxa.validate_track(path.name, data)
                sizes = [len(t["data"]) for t in self._tracks] + [len(data)]
                if pxa.packed_size(sizes) > pxa.CAPACITY:
                    raise pxa.PxaError("加入這首會超過音樂容量（12MB），請先移除其他音樂")
                if len(self._tracks) >= pxa.MAX_FILES:
                    raise pxa.PxaError(f"最多只能放 {pxa.MAX_FILES} 首")
            except pxa.PxaError as exc:
                rejected.append({"name": path.name, "message": exc.user_message})
                continue
            except OSError as exc:
                log.warning("cannot read %s: %s", path, exc)
                rejected.append({"name": path.name, "message": f"無法讀取檔案：{exc.strerror or exc}"})
                continue
            self._tracks.append({"name": path.name, "data": data, "rate": info.sample_rate,
                                 "channels": info.channels})
        return self._music_state(rejected)

    def remove_track(self, index: int) -> dict:
        if not isinstance(index, int) or not 0 <= index < len(self._tracks):
            return _fail(MSG_BAD_INDEX)
        del self._tracks[index]
        return self._music_state()

    def move_track(self, src: int, dst: int) -> dict:
        n = len(self._tracks)
        if not all(isinstance(i, int) and 0 <= i < n for i in (src, dst)):
            return _fail(MSG_BAD_INDEX)
        self._tracks.insert(dst, self._tracks.pop(src))
        return self._music_state()

    def track_data_url(self, index: int) -> dict:
        if not isinstance(index, int) or not 0 <= index < len(self._tracks):
            return _fail(MSG_BAD_INDEX)
        b64 = base64.b64encode(self._tracks[index]["data"]).decode("ascii")
        return {"ok": True, "url": f"data:audio/mpeg;base64,{b64}"}

    # ---- board ----------------------------------------------------------------
    def find_board(self) -> dict:
        try:
            ports = find_board_ports(self._list_ports())
        except Exception as exc:  # noqa: BLE001
            log.exception("listing serial ports failed")
            return {"ports": [], "message": to_board_error(exc).user_message}
        if not ports:
            return {"ports": [], "message": MSG_NO_BOARD}
        if len(ports) == 1:
            return {"ports": ports, "message": f"找到板子：{ports[0]}"}
        return {"ports": ports, "message": f"找到 {len(ports)} 塊板子，請選擇要燒錄的那一塊"}

    def _close_pending(self) -> None:
        if self._pending is not None:
            self._pending[0].close()
            self._pending = None

    def prepare_flash(self, layout: dict, port: Optional[str]) -> dict:
        """Connects, reads the board and returns what will be written: {"ok","kind","notes"}."""
        if not self._lock.acquire(blocking=False):
            return _fail(MSG_BUSY)
        flasher = None
        try:
            self._close_pending()
            gif = None
            if self._gif is not None:
                frames, lay, brightness, swap = self._render(layout)
                gif = pxg.pack(frames, lay.chain, brightness, swap)
            audio = pxa.pack([pxa.Track(t["name"], t["data"]) for t in self._tracks]) if self._tracks else None
            if not port:
                ports = find_board_ports(self._list_ports())
                if not ports:
                    return _fail(MSG_NO_BOARD)
                port = ports[0]
            images = self._load_images()
            flasher = self._flasher_factory(port, self._progress)
            flasher.connect()
            plan = plan_flash(read_state(flasher), images, firmware.bundled_app_info(images), gif, audio)
            self._pending, flasher = (flasher, plan), None
            log.info("flash plan on %s: %s %s", port, plan.kind, [hex(o) for o, _ in plan.regions])
            return {"ok": True, "kind": plan.kind, "notes": plan.notes,
                    "bytes": sum(len(d) for _, d in plan.regions)}
        except (gifproc.GifError, pxa.PxaError) as exc:
            return _fail(exc.user_message)
        except BoardError as exc:
            return _fail(exc.user_message)
        except pxg.PxgError as exc:
            log.error("pxg pack failed: %s", exc)
            return _fail(f"GIF 資料無法打包：{exc}")
        except Exception as exc:  # noqa: BLE001
            log.exception("prepare_flash failed")
            return _fail(to_board_error(exc).user_message)
        finally:
            if flasher is not None:
                flasher.close()
            self._lock.release()

    def confirm_flash(self) -> dict:
        if not self._lock.acquire(blocking=False):
            return _fail(MSG_BUSY)
        try:
            if self._pending is None:
                return _fail(MSG_NO_PENDING)
            flasher, plan = self._pending
            total = sum(len(d) for _, d in plan.regions) or 1
            done = 0
            for offset, data in plan.regions:
                base = done
                flasher.progress = lambda stage, f, base=base, size=len(data): self._progress(
                    "write", min(1.0, (base + f * size) / total))
                flasher.write([(offset, data)])
                done += len(data)
            flasher.hard_reset()
            log.info("flash done: %s", plan.kind)
            return {"ok": True, "message": "燒錄完成，板子已重新啟動"}
        except BoardError as exc:
            return _fail(exc.user_message)
        except Exception as exc:  # noqa: BLE001
            log.exception("confirm_flash failed")
            return _fail(to_board_error(exc).user_message)
        finally:
            self._close_pending()
            self._lock.release()

    def cancel_flash(self) -> dict:
        with self._lock:
            self._close_pending()
        return {"ok": True}


def run() -> None:
    import webview  # imported here so the API can be tested without a GUI toolkit
    from webview.dom import DOMEventHandler

    api = StudioApi()
    window = webview.create_window("PixelBoard Studio", url=str(UI_DIR / "index.html"), js_api=api,
                                   width=1180, height=820, min_size=(980, 700))
    api._window = window

    def on_drop(event):
        files = (event.get("dataTransfer") or {}).get("files") or []
        paths = [f.get("pywebviewFullPath") for f in files if f.get("pywebviewFullPath")]
        if paths:
            window.evaluate_js(f"window.studio.onFilesDropped({json.dumps(paths)})")

    def on_loaded():
        doc = window.dom.document
        doc.events.dragenter += DOMEventHandler(lambda e: None, prevent_default=True, stop_propagation=True)
        doc.events.dragover += DOMEventHandler(lambda e: None, prevent_default=True, stop_propagation=True,
                                               debounce=200)
        doc.events.drop += DOMEventHandler(on_drop, prevent_default=True, stop_propagation=True)

    window.events.loaded += on_loaded
    webview.start(gui="edgechromium")
