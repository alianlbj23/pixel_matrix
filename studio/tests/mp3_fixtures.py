"""Tiny valid MP3 streams for tests: silent MPEG frames (all-zero side info decodes to silence)."""


def frames(header: bytes, frame_len: int, count: int = 20) -> bytes:
    return (header + bytes(frame_len - 4)) * count


def stereo_44k(count: int = 20) -> bytes:
    """MPEG-1 Layer III, 128 kbps, 44.1 kHz, joint stereo: 417-byte frames."""
    return frames(bytes([0xFF, 0xFB, 0x90, 0x64]), 417, count)


def mono_44k(count: int = 20) -> bytes:
    """Same as stereo_44k but channel mode 11 (mono)."""
    return frames(bytes([0xFF, 0xFB, 0x90, 0xC4]), 417, count)


def id3v2(tag_body: bytes = b"\x00" * 300) -> bytes:
    size = len(tag_body)
    syncsafe = bytes([(size >> 21) & 0x7F, (size >> 14) & 0x7F, (size >> 7) & 0x7F, size & 0x7F])
    return b"ID3\x03\x00\x00" + syncsafe + tag_body
