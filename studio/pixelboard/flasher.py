"""Thin wrapper over the esptool 5 Python API with progress reporting and friendly errors."""
import logging
import threading
from typing import Callable, Optional

from .board import GIF_OFFSET, BoardError

log = logging.getLogger(__name__)

BAUD = 460800
CONNECT_ATTEMPTS = 3

MSG_PORT_BUSY = "板子被其他程式占用中（例如序列監看器），請關閉後再試"
MSG_CONNECT = "無法讓板子進入燒錄模式：請按住 BOOT 鍵、按一下 RESET、放開 BOOT 後再試"
MSG_INTERRUPTED = "燒錄中斷，請確認 USB 線沒有鬆脫，重新插上後再按一次燒錄"
MSG_UNEXPECTED = "發生未預期的錯誤："

CHUNK = 0x10000          # gif/audio regions are written (and can be stopped) in 64 KiB steps
STOPPABLE_FROM = GIF_OFFSET  # below this: bootloader, partition table, app - always written whole

# progress(stage, fraction[, stoppable]); write_chunked always passes stoppable.
Progress = Callable[..., None]


def to_board_error(exc: Exception) -> BoardError:
    """Maps esptool / pyserial exceptions to a BoardError with a plain-language message."""
    if isinstance(exc, BoardError):
        return exc
    text = str(exc)
    lowered = text.lower()
    if "could not open port" in lowered or "permissionerror" in lowered or "access is denied" in lowered:
        return BoardError(MSG_PORT_BUSY)
    if "failed to connect" in lowered or "no serial data received" in lowered:
        return BoardError(MSG_CONNECT)
    if "write failed" in lowered or "device disconnected" in lowered or "read failed" in lowered \
            or "timed out waiting" in lowered:
        return BoardError(MSG_INTERRUPTED)
    return BoardError(f"{MSG_UNEXPECTED}{text}")


def _make_logger(progress: Progress):
    from esptool.logger import TemplateLogger

    class _ProgressLogger(TemplateLogger):
        """Routes esptool output to the Python log and progress bars to the UI callback."""

        def print(self, *args, **kwargs):
            message = " ".join(str(a) for a in args).strip()
            if message:
                log.info("esptool: %s", message)

        def note(self, message):
            log.info("esptool note: %s", message)

        def warning(self, message, suggestion=None):
            log.warning("esptool: %s", message)

        def error(self, message, suggestion=None):
            log.error("esptool: %s", message)

        def stage(self, finish=False):
            pass

        def progress_bar(self, cur_iter, total_iters, prefix="", suffix="", bar_length=30):
            if total_iters > 0:
                progress(prefix.strip() or "write", min(1.0, cur_iter / total_iters))

        def set_verbosity(self, verbosity):
            pass

    return _ProgressLogger()


class Flasher:
    """One connection to the board. Every failure is raised as BoardError."""

    def __init__(self, port: str, progress: Optional[Progress] = None):
        self.port = port
        self.progress = progress or (lambda *args: None)
        self._esp = None
        self._bar = None  # (done, size, total, stoppable) of the piece write_chunked is writing

    def _on_bar(self, stage: str, fraction: float) -> None:
        """esptool progress bars: inside write_chunked they become overall write progress."""
        bar = self._bar
        if bar is None:
            self.progress(stage, fraction)
            return
        done, size, total, stoppable = bar
        self.progress("write", min(1.0, (done + fraction * size) / total), stoppable)

    def connect(self) -> None:
        try:
            from esptool import cmds
            from esptool.logger import log as esptool_log

            esptool_log.set_logger(_make_logger(self._on_bar))
            self.progress("connect", 0.0)
            esp = cmds.detect_chip(self.port, BAUD, connect_attempts=CONNECT_ATTEMPTS)
            if esp.CHIP_NAME != "ESP32-S3":
                esp._port.close()
                raise BoardError(f"這不是 pixel-board 的控制板（偵測到 {esp.CHIP_NAME}）")
            esp = cmds.run_stub(esp)
            cmds.attach_flash(esp)
            self._esp = esp
            self.progress("connect", 1.0)
            log.info("connected to %s on %s", esp.CHIP_NAME, self.port)
        except Exception as exc:  # noqa: BLE001 - everything is reported to the user
            log.exception("connect failed on %s", self.port)
            raise to_board_error(exc) from exc

    def _require(self):
        if self._esp is None:
            raise BoardError("尚未連線到板子")
        return self._esp

    def write(self, regions: list) -> None:
        esp = self._require()
        try:
            from esptool import cmds

            cmds.write_flash(esp, [(offset, data) for offset, data in regions])
        except Exception as exc:  # noqa: BLE001
            log.exception("write failed")
            raise to_board_error(exc) from exc

    def _write_piece(self, offset: int, data: bytes) -> None:
        esp = self._require()
        try:
            from esptool import cmds

            cmds.write_flash(esp, [(offset, data)])
        except Exception as exc:  # noqa: BLE001
            log.exception("write failed at 0x%x", offset)
            raise to_board_error(exc) from exc

    def write_chunked(self, regions: list, stop: threading.Event, chunk: int = CHUNK) -> bool:
        """Writes [(offset, bytes)] in order; returns False if `stop` ended it early.

        Regions below STOPPABLE_FROM (firmware) are written whole and never interrupted, so a
        stop can not leave the board unable to boot. gif/audio regions are written in `chunk`
        steps and `stop` is checked before each step. Progress is reported over all regions as
        progress("write", fraction, stoppable).
        """
        if chunk <= 0 or chunk % 0x1000:
            raise ValueError(f"chunk must be a positive multiple of 4096, got {chunk}")
        self._require()
        total = sum(len(data) for _, data in regions) or 1
        done = 0
        for offset, data in regions:
            stoppable = offset >= STOPPABLE_FROM
            step = chunk if stoppable else max(1, len(data))
            for start in range(0, len(data), step):
                if stoppable and stop.is_set():
                    log.info("flash stopped before 0x%x", offset + start)
                    return False
                piece = data[start:start + step]
                self.progress("write", min(1.0, done / total), stoppable)
                self._bar = (done, len(piece), total, stoppable)
                try:
                    self._write_piece(offset + start, piece)
                finally:
                    self._bar = None
                done += len(piece)
                self.progress("write", min(1.0, done / total), stoppable)
        return True

    def read(self, offset: int, size: int) -> bytes:
        esp = self._require()
        try:
            from esptool import cmds

            data = cmds.read_flash(esp, offset, size, no_progress=True)
        except Exception as exc:  # noqa: BLE001
            log.exception("read failed at 0x%x", offset)
            raise to_board_error(exc) from exc
        if data is None or len(data) != size:
            raise BoardError(MSG_INTERRUPTED)
        return data

    def hard_reset(self) -> None:
        esp = self._require()
        try:
            from esptool import cmds

            cmds.reset_chip(esp, "hard-reset")
        except Exception as exc:  # noqa: BLE001
            log.exception("reset failed")
            raise to_board_error(exc) from exc

    def close(self) -> None:
        if self._esp is not None:
            try:
                self._esp._port.close()
            except Exception:  # noqa: BLE001
                log.exception("closing %s failed", self.port)
            self._esp = None
