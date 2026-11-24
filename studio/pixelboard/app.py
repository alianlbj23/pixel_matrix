"""pywebview window and the Python API the HTML UI calls (window.pywebview.api.*).

Every public StudioApi method returns a JSON-serialisable dict and never raises to JS:
failures come back as {"ok": False, "message": <plain-language Chinese text>}.
(can_close() is the exception: it is the window's closing handler and returns a bool.)
Flashing is two steps so the user can confirm what will be written:
prepare_flash() connects and plans, then confirm_flash() writes (or cancel_flash()).
From prepare_flash() until confirm/cancel finishes every editing method is locked
(MSG_LOCKED); stop_flash() may be called from another thread while confirm_flash() writes.
pywebview calls each API method on its own thread, so shared state is guarded by _state.
"""
import base64
import json
import logging
import math
import os
import pathlib
import threading
import time
from typing import Callable, Optional

from . import firmware, gifproc, layout, pxa, pxg, serialcmd
from .board import MSG_NOTHING, BoardError, find_board_ports, list_system_ports, plan_flash, read_state
from .flasher import Flasher, to_board_error

log = logging.getLogger(__name__)

UI_DIR = pathlib.Path(__file__).resolve().parent / "ui"
MSG_NO_BOARD = "找不到板子。請用可傳資料的 USB 線接到板子的 Type-C 資料口（不是 POWER 口）"
MSG_BUSY = "正在燒錄中，請稍候"
MSG_LOCKED = "燒錄中，請先停止燒錄"
MSG_STOPPED = "已停止燒錄：板子上的 GIF/音樂可能不完整，請重新燒錄"
MSG_NO_GIF = "請先加入一個 GIF"
MSG_NO_PENDING = "請重新按一次「燒錄」"
MSG_BAD_INDEX = "找不到這首音樂"
MSG_BAD_LAYOUT = "設定值不正確，請重新調整"
MSG_BAD_PANEL = "找不到這塊面板"
MSG_BAD_POSITION = "面板位置不正確，請重新拖曳"
MSG_BAD_PORT = "板子的連接埠名稱不正確，請重新尋找板子"
MSG_CANVAS_TOO_BIG = "面板排得太開了，整體範圍不能超過 {limit} x {limit} 個 LED，請把面板靠近一點"
MSG_MOVE_OVERLAP = "面板不能重疊，已放回原位"
MSG_ROTATE_OVERLAP = "轉了之後會跟其他面板重疊，請先把它移開一點再旋轉"
MSG_SAVE_FAILED = "排列已更新，但無法儲存設定檔（下次開啟會恢復舊排列）："
MSG_IDENTIFY_SHOWING = "辨識畫面還在顯示中，請稍候"
MSG_FIRMWARE_UP_TO_DATE = "板子上的韌體已經是最新版，不需要更新"
MAX_TRACK_BYTES = pxa.CAPACITY
MAX_CANVAS = 1024          # LED px per side; bounds memory for previews and a corrupt layout.json
IDENTIFY_HOLD_S = 5.5      # firmware shows the identify screen 5 s; a second command meanwhile replies late


def _fail(message: str) -> dict:
    return {"ok": False, "message": message}


def default_layout_path() -> pathlib.Path:
    """%LOCALAPPDATA%\\PixelBoard-Studio\\layout.json on Windows, ~/.pixelboard-studio/layout.json elsewhere."""
    base = os.environ.get("LOCALAPPDATA", "")
    if os.name == "nt" and base.strip():
        return pathlib.Path(base) / "PixelBoard-Studio" / "layout.json"
    return pathlib.Path.home() / ".pixelboard-studio" / "layout.json"


def _as_int(value, lo: int, hi: int) -> int:
    """An int from JS: ints and integral floats in lo..hi; bools, strings and fractions raise ValueError."""
    if isinstance(value, bool):
        raise ValueError(f"bool is not an int: {value!r}")
    if isinstance(value, float):
        if not math.isfinite(value) or not value.is_integer():
            raise ValueError(f"not an integer: {value!r}")
        value = int(value)
    if not isinstance(value, int) or not lo <= value <= hi:
        raise ValueError(f"expected an int in {lo}..{hi}, got {value!r}")
    return value


def _canvas_error(arr: layout.Arrangement) -> Optional[str]:
    w, h = layout.bounds(arr)
    if w > MAX_CANVAS or h > MAX_CANVAS:
        return MSG_CANVAS_TOO_BIG.format(limit=MAX_CANVAS)
    return None


def _load_arrangement(path: pathlib.Path) -> layout.Arrangement:
    arr = layout.normalize(layout.load(path))  # load() never raises; falls back to one panel
    if _canvas_error(arr):
        log.warning("layout file %s spans %s LED px (limit %d), using one panel", path, layout.bounds(arr),
                    MAX_CANVAS)
        return layout.preset("row", 1)
    return arr


def _placement_from(d) -> tuple:
    """Validates the placement dict coming from JS. Returns (gifproc.Placement, brightness)."""
    if not isinstance(d, dict):
        raise gifproc.GifError(MSG_BAD_LAYOUT)
    try:
        mode, scale = d["mode"], d["scale"]
        offset_x = _as_int(d["offset_x"], -gifproc.MAX_OFFSET, gifproc.MAX_OFFSET)
        offset_y = _as_int(d["offset_y"], -gifproc.MAX_OFFSET, gifproc.MAX_OFFSET)
        brightness = _as_int(d["brightness"], pxg.MIN_BRIGHTNESS, pxg.MAX_BRIGHTNESS)
    except (KeyError, TypeError, ValueError) as exc:
        log.warning("bad placement %r: %s", d, exc)
        raise gifproc.GifError(MSG_BAD_LAYOUT) from exc
    if not isinstance(mode, str):
        raise gifproc.GifError(MSG_BAD_LAYOUT)
    if isinstance(scale, int) and not isinstance(scale, bool):
        scale = float(scale)
    placement = gifproc.Placement(mode=mode, offset_x=offset_x, offset_y=offset_y, scale=scale)
    placement.validate()  # mode and scale range (rejects bools/strings) with its own messages
    return placement, brightness


def _panel_rects(arr: layout.Arrangement) -> list:
    return [{"index": p.index, "x": p.x, "y": p.y, "w": w, "h": h, "rotation": p.rotation}
            for p in arr.panels for w, h in [layout.panel_size(p.rotation)]]


def _keep_panels(rgb: bytes, cw: int, ch: int, rects: list) -> bytes:
    """Canvas with every pixel outside the panels set to black (gaps are never shown or flashed)."""
    out = bytearray(len(rgb))
    for r in rects:
        x0, x1 = r["x"] * 3, (r["x"] + r["w"]) * 3
        for y in range(r["y"], min(ch, r["y"] + r["h"])):
            row = y * cw * 3
            out[row + x0:row + x1] = rgb[row + x0:row + x1]
    return bytes(out)


class StudioApi:
    def __init__(self, list_ports: Callable = None, flasher_factory: Callable = None,
                 images_loader: Callable = None, layout_path=None, identify_fn: Callable = None,
                 clock: Callable = None):
        self._window = None  # set by run(); underscore keeps it out of the JS bridge
        self._list_ports = list_ports or list_system_ports
        self._flasher_factory = flasher_factory or Flasher
        self._load_images = images_loader or firmware.load_images
        self._identify = identify_fn or serialcmd.identify
        self._clock = clock or time.monotonic
        self._layout_path = pathlib.Path(layout_path) if layout_path is not None else default_layout_path()
        self._lock = threading.Lock()     # one board operation (prepare/confirm/cancel/identify) at a time
        self._state = threading.RLock()   # guards the editable state below and _flashing
        self._stop = threading.Event()    # set by stop_flash(), read by Flasher.write_chunked
        self._flashing = False            # prepare_flash started .. confirm/cancel finished: editing locked
        self._identify_until = 0.0        # clock() time until the identify screen is still showing
        self._gif: Optional[gifproc.SourceGif] = None
        self._tracks: list = []   # dicts: name, data, rate, channels
        self._arr = _load_arrangement(self._layout_path)
        self._pending = None      # (flasher, plan) between prepare_flash and confirm_flash

    # ---- progress / UI bridge -------------------------------------------------
    def _progress(self, stage: str, fraction: float, stoppable: bool = False) -> None:
        if self._window is not None:
            self._window.evaluate_js(f"window.studio.onProgress({json.dumps(stage)}, {fraction:.3f}, "
                                     f"{'true' if stoppable else 'false'})")

    def _locked(self) -> Optional[dict]:
        return _fail(MSG_LOCKED) if self._flashing else None

    def pick_files(self, kind: str) -> dict:
        """Opens a file dialog; kind is "gif" or "mp3". Returns {"ok", "paths"}."""
        if self._window is None:
            return _fail("視窗尚未就緒")
        import webview

        types = ("GIF 動畫 (*.gif)",) if kind == "gif" else ("MP3 音樂 (*.mp3)",)
        paths = self._window.create_file_dialog(webview.FileDialog.OPEN, allow_multiple=(kind == "mp3"),
                                                file_types=types)
        return {"ok": True, "paths": list(paths or [])}

    # ---- arrangement ----------------------------------------------------------
    def _arrangement_state(self, arr: layout.Arrangement) -> dict:
        return {"ok": True, "arrangement": layout.to_dict(arr), "grid": layout.grid_shape(arr) is not None,
                "max_frames": pxg.max_frames(arr.n), "power_warning": arr.n >= 3}

    def _apply(self, arr: layout.Arrangement) -> dict:
        """Validates, stores and saves a new arrangement (call with _state held)."""
        arr = layout.normalize(arr)
        layout.validate(arr)
        too_big = _canvas_error(arr)
        if too_big:
            return {**self._arrangement_state(self._arr), "ok": False, "message": too_big}
        self._arr = arr
        state = self._arrangement_state(arr)
        try:
            layout.save(self._layout_path, arr)
        except OSError as exc:  # save() already logged it; the change still applies to this session
            state["message"] = f"{MSG_SAVE_FAILED}{exc.strerror or exc}"
        return state

    def _edit_arrangement(self, change: Callable) -> dict:
        with self._state:
            locked = self._locked()
            if locked:
                return locked
            try:
                return change(self._arr)
            except ValueError as exc:
                log.warning("bad arrangement argument: %s", exc)
                return _fail(MSG_BAD_LAYOUT)
            except layout.LayoutError as exc:
                return _fail(exc.user_message)

    def get_arrangement(self) -> dict:
        """Read-only, so it stays available while flashing."""
        with self._state:
            return self._arrangement_state(self._arr)

    def set_panel_count(self, n) -> dict:
        def change(arr):
            try:
                count = _as_int(n, 1, layout.MAX_PANELS)
            except ValueError:
                return _fail(f"面板數量必須是 1 到 {layout.MAX_PANELS} 塊")
            return self._apply(layout.resize(arr, count))
        return self._edit_arrangement(change)

    def apply_preset(self, name) -> dict:
        def change(arr):
            if not isinstance(name, str):
                return _fail("不認識的排列方式")
            return self._apply(layout.preset(name, arr.n))
        return self._edit_arrangement(change)

    def _panel_index(self, arr, index) -> int:
        try:
            return _as_int(index, 0, arr.n - 1)
        except ValueError as exc:
            raise layout.LayoutError(MSG_BAD_PANEL) from exc

    def move_panel(self, index, x, y) -> dict:
        def change(arr):
            i = self._panel_index(arr, index)
            try:
                px, py = _as_int(x, -MAX_CANVAS, MAX_CANVAS), _as_int(y, -MAX_CANVAS, MAX_CANVAS)
            except ValueError as exc:
                log.warning("bad panel position (%r, %r): %s", x, y, exc)
                return _fail(MSG_BAD_POSITION)
            moved = layout.snap(arr, i, px, py)
            if moved is arr:  # snap() hands back the same object when the drop would overlap
                return {**self._arrangement_state(arr), "ok": False, "message": MSG_MOVE_OVERLAP}
            return self._apply(moved)
        return self._edit_arrangement(change)

    def rotate_panel(self, index) -> dict:
        def change(arr):
            i = self._panel_index(arr, index)
            turned = layout.Arrangement(tuple(p._replace(rotation=(p.rotation + 90) % 360) if p.index == i else p
                                              for p in arr.panels))
            try:
                layout.validate(turned)
            except layout.LayoutError:
                return {**self._arrangement_state(arr), "ok": False, "message": MSG_ROTATE_OVERLAP}
            return self._apply(turned)
        return self._edit_arrangement(change)

    # ---- GIF ------------------------------------------------------------------
    def open_gif(self, path: str) -> dict:
        locked = self._locked()
        if locked:
            return locked
        try:
            src = gifproc.load(path)
        except gifproc.GifError as exc:
            return _fail(exc.user_message)
        except Exception as exc:  # noqa: BLE001
            log.exception("open_gif failed")
            return _fail(f"無法開啟 GIF：{exc}")
        with self._state:
            locked = self._locked()
            if locked:
                return locked
            self._gif = src
        return {"ok": True, "name": src.name, "frames": len(src.frames)}

    @staticmethod
    def _render(src: Optional[gifproc.SourceGif], placement: gifproc.Placement, arr: layout.Arrangement) -> list:
        """Canvas frames [(rgb, ms)] of size layout.bounds(arr) with the gaps between panels blacked out."""
        if src is None:
            raise gifproc.GifError(MSG_NO_GIF)
        limit = pxg.max_frames(arr.n)
        if len(src.frames) > limit:
            raise gifproc.GifError(f"目前 {arr.n} 塊面板最多 {limit} 格，這個 GIF 有 {len(src.frames)} 格")
        cw, ch = layout.bounds(arr)
        rects = _panel_rects(arr)
        return [(_keep_panels(rgb, cw, ch, rects), ms) for rgb, ms in gifproc.render(src, placement, arr)]

    def render_preview(self, placement: dict) -> dict:
        with self._state:
            locked = self._locked()
            if locked:
                return locked
            src, arr = self._gif, self._arr
        try:
            parsed, brightness = _placement_from(placement)
            frames = self._render(src, parsed, arr)
        except gifproc.GifError as exc:
            return _fail(exc.user_message)
        except Exception as exc:  # noqa: BLE001
            log.exception("render_preview failed")
            return _fail(f"預覽失敗：{exc}")
        w, h = layout.bounds(arr)
        return {"ok": True, "width": w, "height": h, "panels": _panel_rects(arr), "brightness": brightness,
                "max_frames": pxg.max_frames(arr.n),
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
        with self._state:
            locked = self._locked()
            if locked:
                return locked
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
                        raise pxa.PxaError(f"加入這首會超過音樂容量（{pxa.CAPACITY // 1048576}MB），請先移除其他音樂")
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

    def _track_index(self, index) -> Optional[int]:
        try:
            return _as_int(index, 0, len(self._tracks) - 1)
        except ValueError:
            return None

    def remove_track(self, index: int) -> dict:
        with self._state:
            locked = self._locked()
            if locked:
                return locked
            i = self._track_index(index)
            if i is None:
                return _fail(MSG_BAD_INDEX)
            del self._tracks[i]
            return self._music_state()

    def move_track(self, src: int, dst: int) -> dict:
        with self._state:
            locked = self._locked()
            if locked:
                return locked
            a, b = self._track_index(src), self._track_index(dst)
            if a is None or b is None:
                return _fail(MSG_BAD_INDEX)
            self._tracks.insert(b, self._tracks.pop(a))
            return self._music_state()

    def track_data_url(self, index: int) -> dict:
        with self._state:
            i = self._track_index(index)
            if i is None:
                return _fail(MSG_BAD_INDEX)
            data = self._tracks[i]["data"]
        b64 = base64.b64encode(data).decode("ascii")
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

    def _resolve_port(self, port) -> str:
        """The given port, or the first board found; raises BoardError."""
        if port is not None and port != "":
            if not isinstance(port, str) or len(port) > 256:
                raise BoardError(MSG_BAD_PORT)
            return port
        ports = find_board_ports(self._list_ports())
        if not ports:
            raise BoardError(MSG_NO_BOARD)
        return ports[0]

    def identify(self, port: Optional[str]) -> dict:
        """Shows each panel's number and "up" arrow on the real panels for 5 s (no flashing)."""
        with self._state:
            locked = self._locked()
            if locked:
                return locked
            n = self._arr.n
        if self._clock() < self._identify_until:
            return _fail(MSG_IDENTIFY_SHOWING)
        if not self._lock.acquire(blocking=False):
            return _fail(MSG_BUSY)
        try:
            message = self._identify(self._resolve_port(port), n)
            self._identify_until = self._clock() + IDENTIFY_HOLD_S
            return {"ok": True, "message": message}
        except BoardError as exc:
            return _fail(exc.user_message)
        except Exception as exc:  # noqa: BLE001
            log.exception("identify failed")
            return _fail(to_board_error(exc).user_message)
        finally:
            self._lock.release()

    def _close_pending(self, reset: bool = False) -> None:
        if self._pending is not None:
            self._pending[0].close(reset=reset)
            self._pending = None

    def _end_flashing(self) -> None:
        with self._state:
            self._flashing = False

    def prepare_flash(self, placement: Optional[dict], port: Optional[str], firmware_only: bool = False) -> dict:
        """Connects, reads the board and returns what will be written: {"ok","kind","notes","bytes"}.

        firmware_only=True plans bootloader/partition table/app only (placement is ignored); if the
        board already runs this firmware the answer is {"ok": False, "up_to_date": True, ...}.
        Editing is locked from here until confirm_flash()/cancel_flash() finishes.
        """
        if not isinstance(firmware_only, bool):
            return _fail(MSG_BAD_LAYOUT)
        if not self._lock.acquire(blocking=False):
            return _fail(MSG_BUSY)
        flasher = None
        prepared = False
        try:
            self._close_pending()
            with self._state:
                self._flashing = True  # snapshot below is what gets written; no edits from now on
                self._stop.clear()
                src, arr, tracks = self._gif, self._arr, list(self._tracks)
            gif = audio = None
            if not firmware_only:
                if src is not None:
                    parsed, brightness = _placement_from(placement)
                    cw, ch = layout.bounds(arr)
                    frames = [(layout.chain_frame(rgb, cw, ch, arr), ms) for rgb, ms in self._render(src, parsed, arr)]
                    gif = pxg.pack(frames, arr, brightness, parsed.mode == "split")
                if tracks:
                    audio = pxa.pack([pxa.Track(t["name"], t["data"]) for t in tracks])
            port = self._resolve_port(port)
            images = self._load_images()
            flasher = self._flasher_factory(port, self._progress)
            flasher.connect()
            state = read_state(flasher, images["app"])
            try:
                plan = plan_flash(state, images, firmware.bundled_app_info(images), gif, audio)
            except BoardError as exc:
                if firmware_only and exc.user_message == MSG_NOTHING:
                    return {"ok": False, "up_to_date": True, "message": MSG_FIRMWARE_UP_TO_DATE}
                raise
            self._pending, flasher = (flasher, plan), None
            prepared = True
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
                flasher.close(reset=True)  # connected but nothing written: leave download mode
            if not prepared:
                self._end_flashing()
            self._lock.release()

    def confirm_flash(self) -> dict:
        """Writes the prepared plan. A stop_flash() during gif/audio ends it early (board is reset)."""
        if not self._lock.acquire(blocking=False):
            return _fail(MSG_BUSY)
        reset_done = False
        try:
            if self._pending is None:
                return _fail(MSG_NO_PENDING)
            flasher, plan = self._pending
            flasher.progress = self._progress
            finished = flasher.write_chunked(plan.regions, self._stop)
            flasher.hard_reset()
            reset_done = True
            if not finished:
                log.info("flash stopped by the user: %s", plan.kind)
                return {"ok": False, "stopped": True, "message": MSG_STOPPED}
            log.info("flash done: %s", plan.kind)
            return {"ok": True, "message": "燒錄完成，板子已重新啟動"}
        except BoardError as exc:
            return _fail(exc.user_message)
        except Exception as exc:  # noqa: BLE001
            log.exception("confirm_flash failed")
            return _fail(to_board_error(exc).user_message)
        finally:
            self._close_pending(reset=not reset_done)  # failed before our own reset: leave download mode
            self._end_flashing()
            self._lock.release()

    def cancel_flash(self) -> dict:
        with self._lock:
            self._close_pending(reset=True)  # nothing written: restart the board out of download mode
            self._end_flashing()
        return {"ok": True}

    def can_close(self) -> bool:
        """Window close requested (pywebview `closing` handler): False cancels it while flashing.

        Runs on the GUI thread, so the UI message is sent from a helper thread: evaluate_js
        waits for the GUI thread and would deadlock here.
        """
        with self._state:
            flashing = self._flashing
        if not flashing:
            return True
        log.info("window close blocked: flashing in progress")
        window = self._window
        if window is not None:
            code = f"window.studio.onCloseBlocked({json.dumps(MSG_LOCKED)})"

            def notify():
                try:
                    window.evaluate_js(code)
                except Exception:  # noqa: BLE001 - nothing else can show it; the close is still cancelled
                    log.exception("could not show the close-blocked message")

            threading.Thread(target=notify, name="close-blocked", daemon=True).start()
        return False

    def stop_flash(self) -> dict:
        """Asks a running confirm_flash() to stop at the next 64 KiB step of the gif/audio data.

        Deliberately does not take _lock (confirm_flash holds it while writing).
        """
        self._stop.set()
        log.info("stop requested")
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
    window.events.closing += api.can_close  # returning False keeps the window open while flashing
    webview.start(gui="edgechromium")
