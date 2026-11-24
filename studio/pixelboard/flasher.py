"""Thin wrapper over the esptool 5 Python API with progress reporting and friendly errors."""
import logging
from typing import Callable, Optional

from .board import BoardError

log = logging.getLogger(__name__)

BAUD = 460800
CONNECT_ATTEMPTS = 3

MSG_PORT_BUSY = "板子被其他程式占用中（例如序列監看器），請關閉後再試"
MSG_CONNECT = "無法讓板子進入燒錄模式：請按住 BOOT 鍵、按一下 RESET、放開 BOOT 後再試"
MSG_INTERRUPTED = "燒錄中斷，請確認 USB 線沒有鬆脫，重新插上後再按一次燒錄"
MSG_UNEXPECTED = "發生未預期的錯誤："

Progress = Callable[[str, float], None]


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
        self.progress = progress or (lambda stage, fraction: None)
        self._esp = None

    def connect(self) -> None:
        try:
            from esptool import cmds
            from esptool.logger import log as esptool_log

            esptool_log.set_logger(_make_logger(self.progress))
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
