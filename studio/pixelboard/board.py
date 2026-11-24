"""Finding the board's serial port and user-facing board errors."""
from typing import NamedTuple, Optional

# ESP32-S3 built-in USB Serial/JTAG.
BOARD_VID = 0x303A
BOARD_PID = 0x1001


class BoardError(Exception):
    """Error with a plain-language (Traditional Chinese) message for the user."""

    def __init__(self, user_message: str):
        super().__init__(user_message)
        self.user_message = user_message


class PortInfo(NamedTuple):
    device: str
    vid: Optional[int]
    pid: Optional[int]
    description: str


def find_board_ports(ports: list) -> list:
    """Devices of all pixel-board (ESP32-S3 USB Serial/JTAG) ports, sorted by name."""
    return sorted(p.device for p in ports if p.vid == BOARD_VID and p.pid == BOARD_PID)


def list_system_ports() -> list:
    from serial.tools import list_ports  # imported lazily so pure helpers work without pyserial

    return [PortInfo(p.device, p.vid, p.pid, p.description or "") for p in list_ports.comports()]
