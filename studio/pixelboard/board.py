"""Finding the board's serial port and user-facing board errors."""
import hashlib
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


# ---------------------------------------------------------------------------
# Board state and flash planning
# ---------------------------------------------------------------------------
APP_OFFSET = 0x10000
APP_HEAD_SIZE = 256
PARTITION_TABLE_OFFSET = 0x8000
PARTITION_TABLE_SIZE = 0xC00
GIF_OFFSET = 0x210000
AUDIO_OFFSET = 0x510000
PROJECT_NAME = "pixel-board"
_APP_DESC = 0x20          # esp_app_desc_t follows the image + first segment headers
_APP_DESC_MAGIC = 0xABCD5432

NOTE_FIRST_INSTALL = "這塊板子還沒有安裝 pixel-board 韌體，會先安裝完整韌體（約 1 分鐘）"
NOTE_UPDATE_FIRMWARE = "板子上的韌體版本不同，會一併更新韌體"
NOTE_REPAIR_FIRMWARE = "板子上的韌體不完整或已損壞，會重新寫入韌體"
NOTE_REPLACE_MUSIC = "板子上的音樂會被這份清單取代"
NOTE_KEEP_MUSIC = "沒有加入音樂：保留板子上原本的音樂"
NOTE_NO_MUSIC = "沒有加入音樂：安裝後板子上沒有背景音樂"
NOTE_READD_MUSIC = "韌體的儲存格式更新了，板子上原本的音樂可能無法播放，請重新加入音樂"
MSG_NOTHING = "沒有要燒錄的內容"


class AppInfo(NamedTuple):
    project: str
    version: str


class BoardState(NamedTuple):
    app: Optional[AppInfo]
    partition_table: bytes
    # MD5 of the board's app region equals the bundled app image; None when not checked
    # (no bundled image given, or the board does not run pixel-board at all).
    app_matches: Optional[bool] = None


class FlashPlan(NamedTuple):
    kind: str       # "first_install" | "update_firmware" | "update_content"
    regions: list   # [(offset, bytes)] in ascending offset order
    notes: list     # plain-language notes shown before flashing
    music_may_be_lost: bool = False  # new partition table, no new music: old music may not play


def _cstr(raw: bytes) -> str:
    return raw.split(b"\0", 1)[0].decode("utf-8", errors="replace")


def parse_app_desc(image_head: bytes) -> Optional[AppInfo]:
    """Reads esp_app_desc_t from the first 256 bytes of an app image; None if absent."""
    if len(image_head) < _APP_DESC + 0x50:
        return None
    magic = int.from_bytes(image_head[_APP_DESC:_APP_DESC + 4], "little")
    if magic != _APP_DESC_MAGIC:
        return None
    version = _cstr(image_head[_APP_DESC + 0x10:_APP_DESC + 0x30])
    project = _cstr(image_head[_APP_DESC + 0x30:_APP_DESC + 0x50])
    return AppInfo(project, version)


def read_state(flasher, bundled_app: Optional[bytes] = None) -> BoardState:
    """Reads what the planner needs from a connected Flasher (raises BoardError).

    With `bundled_app`, a board that runs pixel-board also gets its app region compared by MD5
    (computed on the board) so a half-written app with an intact header is not "up to date".
    """
    head = flasher.read(APP_OFFSET, APP_HEAD_SIZE)
    table = flasher.read(PARTITION_TABLE_OFFSET, PARTITION_TABLE_SIZE)
    app = parse_app_desc(head)
    app_matches = None
    if bundled_app and app is not None and app.project == PROJECT_NAME:
        board_md5 = flasher.flash_md5(APP_OFFSET, len(bundled_app))
        app_matches = board_md5 == hashlib.md5(bundled_app).hexdigest()
    return BoardState(app, table, app_matches)


def plan_flash(state: BoardState, bundled: dict, bundled_app: AppInfo,
               gif: Optional[bytes], audio: Optional[bytes]) -> FlashPlan:
    """Decides which flash regions to write (see the design doc, section 6.5)."""
    table_differs = state.partition_table[:len(bundled["partition_table"])] != bundled["partition_table"]
    if state.app is None or state.app.project != PROJECT_NAME:
        kind = "first_install"
        regions = [(0x0, bundled["bootloader"]), (PARTITION_TABLE_OFFSET, bundled["partition_table"]),
                   (APP_OFFSET, bundled["app"])]
        notes = [NOTE_FIRST_INSTALL]
    elif state.app.version != bundled_app.version or table_differs or state.app_matches is False:
        kind = "update_firmware"
        regions = [(PARTITION_TABLE_OFFSET, bundled["partition_table"])] if table_differs else []
        regions.append((APP_OFFSET, bundled["app"]))
        same_firmware = state.app.version == bundled_app.version and not table_differs
        notes = [NOTE_REPAIR_FIRMWARE if same_firmware else NOTE_UPDATE_FIRMWARE]
    else:
        kind = "update_content"
        regions = []
        notes = []
        if gif is None and audio is None:
            raise BoardError(MSG_NOTHING)
    if gif is not None:
        regions.append((GIF_OFFSET, gif))
    if audio is not None:
        regions.append((AUDIO_OFFSET, audio))
        notes.append(NOTE_REPLACE_MUSIC)
    elif kind == "first_install":
        notes.append(NOTE_NO_MUSIC)
    elif table_differs:
        notes.append(NOTE_READD_MUSIC)
        return FlashPlan(kind, regions, notes, music_may_be_lost=True)
    else:
        notes.append(NOTE_KEEP_MUSIC)
    return FlashPlan(kind, regions, notes)
