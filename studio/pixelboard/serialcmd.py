"""Text commands to the running firmware over the board's USB Serial/JTAG port (no flashing).

Protocol (main/cmd.cpp): the app sends "PXB IDENTIFY <N>\\n"; the firmware answers
"PXB OK IDENTIFY <N>" or "PXB ERR <reason>" (lines end with "\\r\\n" and are mixed with
ESP_LOG output, so only lines starting with "PXB " are considered).
The port is opened with DTR asserted: DTR low resets the ESP32-S3 through USB Serial/JTAG.
"""
import logging
import time
from typing import Callable

import serial

from . import layout
from .board import BoardError
from .flasher import to_board_error

log = logging.getLogger(__name__)

READ_SLICE = 0.1         # s per readline() so the overall deadline is honoured
WRITE_TIMEOUT = 1.0      # s
MAX_LINE = 512           # bytes; longer garbage without a newline is dropped
MAX_REASON = 80          # characters of a firmware error shown to the user

MSG_NO_REPLY = "這塊板子還沒有新版韌體，請先按『安裝/更新韌體』"
MSG_FIRMWARE_ERROR = "板子回報錯誤："


def _check_count(n) -> None:
    if isinstance(n, bool) or not isinstance(n, int) or not 1 <= n <= layout.MAX_PANELS:
        raise ValueError(f"panel count must be an int 1..{layout.MAX_PANELS}, got {n!r}")


def _open(port: str, open_serial: Callable):
    ser = open_serial()
    ser.port = port
    ser.timeout = READ_SLICE
    ser.write_timeout = WRITE_TIMEOUT
    ser.dtr = True   # keep DTR asserted: DTR low resets the board
    ser.rts = False
    ser.open()
    return ser


def _read_reply(ser, n: int, timeout: float) -> str:
    """Returns the payload after "PXB " of the reply to IDENTIFY n; raises BoardError on timeout/ERR."""
    expected_ok = f"OK IDENTIFY {n}"
    deadline = time.monotonic() + timeout
    buf = b""
    while time.monotonic() < deadline:
        buf += ser.readline()
        if not buf.endswith(b"\n"):
            if len(buf) > MAX_LINE:
                log.warning("dropping %d bytes without a line end", len(buf))
                buf = b""
            continue
        line = buf.decode("utf-8", errors="replace").strip()
        buf = b""
        if not line.startswith("PXB "):
            continue  # firmware log output
        payload = line[4:].strip()
        if payload == expected_ok:
            return payload
        if payload.startswith("ERR"):
            reason = payload[3:].strip()[:MAX_REASON] or "unknown"
            log.warning("identify rejected by firmware: %s", reason)
            raise BoardError(f"{MSG_FIRMWARE_ERROR}{reason}")
        log.info("ignoring unrelated reply %r", line)
    log.warning("no identify reply within %.1f s", timeout)
    raise BoardError(MSG_NO_REPLY)


def identify(port: str, n: int, open_serial: Callable = serial.Serial, timeout: float = 1.5) -> str:
    """Asks the firmware to show each panel's number and "up" arrow; returns a message for the user.

    Raises BoardError (plain-language message) when the port cannot be used, the firmware does
    not answer within `timeout` seconds, or it answers with an error. ValueError for a bad n.
    """
    _check_count(n)
    ser = None
    try:
        ser = _open(port, open_serial)
        ser.reset_input_buffer()
        ser.write(f"PXB IDENTIFY {n}\n".encode("ascii"))
        _read_reply(ser, n, timeout)
    except BoardError:
        raise
    except Exception as exc:  # noqa: BLE001 - pyserial errors become plain-language BoardErrors
        log.exception("identify on %s failed", port)
        raise to_board_error(exc) from exc
    finally:
        if ser is not None:
            try:
                ser.close()
            except Exception:  # noqa: BLE001
                log.exception("closing %s failed", port)
    log.info("identify %d panel(s) on %s ok", n, port)
    return f"已在 {n} 塊面板上顯示編號和「上方」箭頭，5 秒後恢復原本的畫面"
