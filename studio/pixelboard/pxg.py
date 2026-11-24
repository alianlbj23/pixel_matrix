"""PXG1: the animation format stored in the board's `gif` flash partition.

Little-endian. 32-byte header:
  0 char[4] "PXG1" | 4 u16 version=1 | 6 u16 panel_w=64 | 8 u16 panel_h=32 | 10 u8 chain (1/2)
  11 u8 brightness (10..128) | 12 u8 flags (bit0 swap panels) | 13 u8[3] 0 | 16 u16 frame_count (1..64)
  18 u16 0 | 20 u32 payload_len | 24 u32 crc32(payload) | 28 u32 0
Payload: frame_count x u16 duration_ms, zero-padded to 4 bytes, then frame_count RGB888 frames
of (64*chain) x 32 pixels, row-major. Must stay in sync with main/pxg.cpp.
"""
import struct
import zlib
from dataclasses import dataclass

MAGIC = b"PXG1"
VERSION = 1
PANEL_W, PANEL_H = 64, 32
MAX_FRAMES = 64
MIN_BRIGHTNESS, MAX_BRIGHTNESS = 10, 128
FLAG_SWAP = 0x01
MAX_BYTES = 0x100000  # size of the `gif` partition
_HEADER = struct.Struct("<4sHHHBBB3xHHII4x")
HEADER_SIZE = _HEADER.size  # 32


class PxgError(Exception):
    """Invalid PXG1 data or parameters."""


@dataclass
class Animation:
    chain: int
    brightness: int
    swap: bool
    frames: list  # [(rgb888_bytes, duration_ms)]


def frame_bytes(chain: int) -> int:
    return PANEL_W * chain * PANEL_H * 3


def _pad4(n: int) -> int:
    return (n + 3) & ~3


def pack(frames: list, chain: int, brightness: int, swap: bool) -> bytes:
    if chain not in (1, 2):
        raise PxgError(f"chain must be 1 or 2, got {chain}")
    if not MIN_BRIGHTNESS <= brightness <= MAX_BRIGHTNESS:
        raise PxgError(f"brightness must be {MIN_BRIGHTNESS}..{MAX_BRIGHTNESS}, got {brightness}")
    if not 1 <= len(frames) <= MAX_FRAMES:
        raise PxgError(f"frame count must be 1..{MAX_FRAMES}, got {len(frames)}")
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
    payload = bytes(durations + pixels)
    data = _HEADER.pack(MAGIC, VERSION, PANEL_W, PANEL_H, chain, brightness,
                        FLAG_SWAP if swap else 0, len(frames), 0, len(payload), zlib.crc32(payload)) + payload
    if len(data) > MAX_BYTES:
        raise PxgError(f"animation is {len(data)} bytes, larger than the {MAX_BYTES} byte partition")
    return data


def unpack(data: bytes) -> Animation:
    if len(data) < HEADER_SIZE:
        raise PxgError("data shorter than the header")
    magic, version, pw, ph, chain, brightness, flags, count, _, payload_len, crc = _HEADER.unpack_from(data)
    if magic != MAGIC:
        raise PxgError("bad magic")
    if version != VERSION:
        raise PxgError(f"unsupported version {version}")
    if (pw, ph) != (PANEL_W, PANEL_H) or chain not in (1, 2):
        raise PxgError(f"unsupported geometry {pw}x{ph} chain {chain}")
    if not 1 <= count <= MAX_FRAMES:
        raise PxgError(f"bad frame count {count}")
    size = frame_bytes(chain)
    expected = _pad4(2 * count) + count * size
    if payload_len != expected or len(data) < HEADER_SIZE + payload_len:
        raise PxgError("truncated or inconsistent payload")
    payload = data[HEADER_SIZE:HEADER_SIZE + payload_len]
    if zlib.crc32(payload) != crc:
        raise PxgError("crc mismatch")
    durations = struct.unpack_from(f"<{count}H", payload)
    base = _pad4(2 * count)
    frames = [(bytes(payload[base + i * size: base + (i + 1) * size]), durations[i]) for i in range(count)]
    return Animation(chain, brightness, bool(flags & FLAG_SWAP), frames)
