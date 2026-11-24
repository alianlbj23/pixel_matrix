"""PXA1: the MP3 playlist stored in the board's `audio` flash partition. Stdlib only.

Little-endian. 32-byte header:
  0 char[4] "PXA1" | 4 u16 version=1 | 6 u16 file_count (0..64) | 8 u32 payload_len
  12 u32 crc32(payload) | 16 u8[16] 0
Payload = file table + data. Table entry (72 bytes): u8[64] UTF-8 name (NUL padded, <= 60 bytes),
u32 offset from the partition start, u32 length. MP3 data follows, each file 4-byte aligned, in
play order. Must stay in sync with main/pxa.cpp.
"""
import struct
import zlib
from typing import NamedTuple

from .mp3info import SUPPORTED_RATES, Info, Mp3Error, probe

MAGIC = b"PXA1"
VERSION = 1
MAX_FILES = 64
MAX_NAME_BYTES = 60   # media::kMaxNameLen in the firmware
NAME_FIELD = 64
CAPACITY = 12 * 1024 * 1024  # size of the `audio` partition
_HEADER = struct.Struct("<4sHHII16x")
_ENTRY = struct.Struct("<64sII")
HEADER_SIZE = _HEADER.size  # 32
ENTRY_SIZE = _ENTRY.size    # 72


class PxaError(Exception):
    """Invalid PXA1 data or track, with a plain-language message for the user."""

    def __init__(self, user_message: str):
        super().__init__(user_message)
        self.user_message = user_message


class Track(NamedTuple):
    name: str
    data: bytes


def _pad4(n: int) -> int:
    return (n + 3) & ~3


def _name_bytes(name: str) -> bytes:
    raw = name.encode("utf-8")
    if not raw or b"\0" in raw:
        raise PxaError("檔名不可以是空的")
    if len(raw) > MAX_NAME_BYTES:
        raise PxaError(f"檔名太長（{len(raw)} bytes），最多 {MAX_NAME_BYTES} bytes（約 20 個中文字），請改短")
    return raw


def validate_track(name: str, data: bytes) -> Info:
    """Checks a track can be stored and played. Returns its format; raises PxaError."""
    _name_bytes(name)
    try:
        info = probe(data)
    except Mp3Error as exc:
        raise PxaError(exc.user_message) from exc
    if info.sample_rate not in SUPPORTED_RATES:
        raise PxaError(f"這個 MP3 的取樣率不受支援（{info.sample_rate} Hz）")
    return info


def packed_size(sizes: list) -> int:
    """Bytes a PXA1 image of files with these sizes takes (for capacity display)."""
    pos = _pad4(HEADER_SIZE + ENTRY_SIZE * len(sizes))
    for size in sizes:
        pos = _pad4(pos + size)
    return pos


def pack(tracks: list) -> bytes:
    if len(tracks) > MAX_FILES:
        raise PxaError(f"最多只能放 {MAX_FILES} 首")
    total = packed_size([len(t.data) for t in tracks])
    if total > CAPACITY:
        raise PxaError(f"音樂超過容量：共 {total / 1048576:.1f}MB，上限 {CAPACITY // 1048576}MB，請移除一些檔案")
    table = bytearray()
    blobs = bytearray()
    pos = _pad4(HEADER_SIZE + ENTRY_SIZE * len(tracks))
    for t in tracks:
        table += _ENTRY.pack(_name_bytes(t.name).ljust(NAME_FIELD, b"\0"), pos, len(t.data))
        blob = t.data + bytes(_pad4(len(t.data)) - len(t.data))
        blobs += blob
        pos += len(blob)
    table_end = HEADER_SIZE + len(table)
    payload = bytes(table) + bytes(_pad4(table_end) - table_end) + bytes(blobs)
    return _HEADER.pack(MAGIC, VERSION, len(tracks), len(payload), zlib.crc32(payload)) + payload


def unpack(data: bytes) -> list:
    if len(data) < HEADER_SIZE:
        raise PxaError("音樂資料損壞（太短）")
    magic, version, count, payload_len, crc = _HEADER.unpack_from(data)
    if magic != MAGIC:
        raise PxaError("音樂資料損壞（bad magic）")
    if version != VERSION:
        raise PxaError(f"不支援的音樂資料版本 {version}")
    if count > MAX_FILES or HEADER_SIZE + ENTRY_SIZE * count > HEADER_SIZE + payload_len:
        raise PxaError("音樂資料損壞（檔案數）")
    end = HEADER_SIZE + payload_len
    if len(data) < end:
        raise PxaError("音樂資料損壞（被截斷）")
    if zlib.crc32(data[HEADER_SIZE:end]) != crc:
        raise PxaError("音樂資料損壞（crc mismatch）")
    table_end = HEADER_SIZE + ENTRY_SIZE * count
    tracks = []
    for i in range(count):
        raw, offset, length = _ENTRY.unpack_from(data, HEADER_SIZE + ENTRY_SIZE * i)
        if offset < table_end or offset + length > end:
            raise PxaError("音樂資料損壞（檔案位置）")
        name = raw.split(b"\0", 1)[0].decode("utf-8", errors="replace")
        tracks.append(Track(name, bytes(data[offset:offset + length])))
    return tracks
