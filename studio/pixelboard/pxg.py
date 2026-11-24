"""PXG: the animation format stored in the board's `gif` flash partition.

Little-endian. 32-byte header (same for v1 and v2):
  0 char[4] "PXG1" | 4 u16 version (1 or 2) | 6 u16 panel_w=64 | 8 u16 panel_h=32
  10 u8 chain (v1: 1/2, v2: 1..8) | 11 u8 brightness (10..128)
  12 u8 flags (bit0 swap panels, v1 only; bit1 split, informational) | 13 u8[3] 0
  16 u16 frame_count (1..64) | 18 u16 0 | 20 u32 payload_len | 24 u32 crc32(payload) | 28 u32 0
Payload v1: frame_count x u16 duration_ms, zero-padded to 4 bytes, then frame_count RGB888 frames
of (64*chain) x 32 pixels, row-major.
Payload v2: durations (as v1), then an arrangement table of chain x 8 bytes (`<hhHH`: x, y,
rotation, 0; normalized canvas coordinates in panel index order), then the frames.
New files are always v2; v1 is still read. Must stay in sync with main/pxg.cpp.
"""
import struct
import zlib
from dataclasses import dataclass

from . import layout

MAGIC = b"PXG1"
VERSION = 2
PANEL_W, PANEL_H = 64, 32
MAX_FRAMES = 64
MAX_CHAIN = 8
MIN_BRIGHTNESS, MAX_BRIGHTNESS = 10, 128
FLAG_SWAP = 0x01
FLAG_SPLIT = 0x02
MAX_BYTES = 3 * 1024 * 1024  # size of the `gif` partition
_HEADER = struct.Struct("<4sHHHBBB3xHHII4x")
_PANEL = struct.Struct("<hhHH")
HEADER_SIZE = _HEADER.size  # 32


class PxgError(Exception):
    """Invalid PXG data or parameters."""


@dataclass
class Animation:
    chain: int
    brightness: int
    swap: bool
    split: bool
    panels: list  # [(x, y, rotation)] for v2, [] for v1
    frames: list  # [(rgb888_bytes, duration_ms)]


def frame_bytes(chain: int) -> int:
    return PANEL_W * chain * PANEL_H * 3


def max_frames(n: int) -> int:
    """Most frames that fit the partition (with 4 KiB headroom for header, durations and table)."""
    return min(MAX_FRAMES, (MAX_BYTES - 4096) // frame_bytes(n))


def _pad4(n: int) -> int:
    return (n + 3) & ~3


def pack(frames: list, arr: layout.Arrangement, brightness: int, split: bool) -> bytes:
    try:
        layout.validate(arr)
        norm = layout.normalize(arr)
    except layout.LayoutError as exc:
        raise PxgError(f"invalid arrangement: {exc.user_message}") from exc
    chain = norm.n
    if not MIN_BRIGHTNESS <= brightness <= MAX_BRIGHTNESS:
        raise PxgError(f"brightness must be {MIN_BRIGHTNESS}..{MAX_BRIGHTNESS}, got {brightness}")
    limit = max_frames(chain)
    if not 1 <= len(frames) <= limit:
        raise PxgError(f"frame count must be 1..{limit} for {chain} panel(s), got {len(frames)}")
    size = frame_bytes(chain)
    durations = bytearray()
    pixels = bytearray()
    for i, (rgb, ms) in enumerate(frames):
        if len(rgb) != size:
            raise PxgError(f"frame {i} is {len(rgb)} bytes, expected {size} for chain {chain}")
        if not 0 < ms <= 0xFFFF:
            raise PxgError(f"frame {i} duration {ms} ms out of range")
        durations += struct.pack("<H", ms)
        pixels += rgb
    durations += bytes(_pad4(len(durations)) - len(durations))
    table = b"".join(_PANEL.pack(p.x, p.y, p.rotation, 0) for p in norm.panels)
    payload = bytes(durations) + table + bytes(pixels)
    data = _HEADER.pack(MAGIC, VERSION, PANEL_W, PANEL_H, chain, brightness,
                        FLAG_SPLIT if split else 0, len(frames), 0, len(payload),
                        zlib.crc32(payload)) + payload
    if len(data) > MAX_BYTES:
        raise PxgError(f"animation is {len(data)} bytes, larger than the {MAX_BYTES} byte partition")
    return data


def unpack(data: bytes) -> Animation:
    if len(data) < HEADER_SIZE:
        raise PxgError("data shorter than the header")
    magic, version, pw, ph, chain, brightness, flags, count, _, payload_len, crc = _HEADER.unpack_from(data)
    if magic != MAGIC:
        raise PxgError("bad magic")
    if version not in (1, 2):
        raise PxgError(f"unsupported version {version}")
    max_chain = 2 if version == 1 else MAX_CHAIN
    if (pw, ph) != (PANEL_W, PANEL_H) or not 1 <= chain <= max_chain:
        raise PxgError(f"unsupported geometry {pw}x{ph} chain {chain}")
    if not 1 <= count <= MAX_FRAMES:
        raise PxgError(f"bad frame count {count}")
    size = frame_bytes(chain)
    table_len = _PANEL.size * chain if version == 2 else 0
    expected = _pad4(2 * count) + table_len + count * size
    if payload_len != expected or len(data) < HEADER_SIZE + payload_len:
        raise PxgError("truncated or inconsistent payload")
    payload = data[HEADER_SIZE:HEADER_SIZE + payload_len]
    if zlib.crc32(payload) != crc:
        raise PxgError("crc mismatch")
    durations = struct.unpack_from(f"<{count}H", payload)
    table_at = _pad4(2 * count)
    panels = [_PANEL.unpack_from(payload, table_at + i * _PANEL.size)[:3] for i in range(chain)] \
        if version == 2 else []
    base = table_at + table_len
    frames = [(bytes(payload[base + i * size: base + (i + 1) * size]), durations[i]) for i in range(count)]
    return Animation(chain, brightness, bool(flags & FLAG_SWAP), bool(flags & FLAG_SPLIT), panels, frames)
