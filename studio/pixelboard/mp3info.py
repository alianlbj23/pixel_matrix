"""Checks that a file is an MPEG audio Layer III (MP3) stream the board can play. Stdlib only."""
from typing import NamedTuple

SUPPORTED_RATES = (8000, 11025, 12000, 16000, 22050, 24000, 32000, 44100, 48000)
_SEARCH_WINDOW = 64 * 1024   # how far past the ID3 tag to look for the first frame
_FRAMES_IN_SYNC = 3

# [version] -> sample rates for index 0..2 (index 3 reserved). Version bits: 0 = MPEG2.5, 2 = MPEG2, 3 = MPEG1.
_RATES = {3: (44100, 48000, 32000), 2: (22050, 24000, 16000), 0: (11025, 12000, 8000)}
_BITRATES_V1 = (0, 32, 40, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320)
_BITRATES_V2 = (0, 8, 16, 24, 32, 40, 48, 56, 64, 80, 96, 112, 128, 144, 160)

MSG_NOT_MP3 = "這個檔案不是 MP3 音樂檔（或已損壞）"
MSG_NOT_LAYER3 = "這個檔案不是 MP3（偵測到其他 MPEG 音訊格式），請轉成 MP3 後再加入"
MSG_BAD_RATE = "這個 MP3 的取樣率不受支援"


class Mp3Error(Exception):
    def __init__(self, user_message: str):
        super().__init__(user_message)
        self.user_message = user_message


class Info(NamedTuple):
    sample_rate: int
    channels: int


class _Header(NamedTuple):
    version: int
    layer: int
    sample_rate: int
    channels: int
    length: int


def _parse_header(data: bytes, pos: int):
    """Returns _Header, or a string naming why it is not a usable Layer III header, or None (no sync)."""
    if pos + 4 > len(data) or data[pos] != 0xFF or (data[pos + 1] & 0xE0) != 0xE0:
        return None
    b1, b2, b3 = data[pos + 1], data[pos + 2], data[pos + 3]
    version = (b1 >> 3) & 0x3
    layer = (b1 >> 1) & 0x3
    if version == 1 or layer == 0:
        return None  # reserved values: not a real header
    if layer != 1:  # 1 = Layer III
        return "layer"
    rate_index = (b2 >> 2) & 0x3
    if rate_index == 3:
        return "rate"
    bitrate_index = b2 >> 4
    if bitrate_index in (0, 15):
        return None  # free-format or invalid bitrate
    rate = _RATES[version][rate_index]
    bitrate = (_BITRATES_V1 if version == 3 else _BITRATES_V2)[bitrate_index] * 1000
    padding = (b2 >> 1) & 0x1
    length = (144 if version == 3 else 72) * bitrate // rate + padding
    channels = 1 if (b3 >> 6) == 0x3 else 2
    return _Header(version, layer, rate, channels, length)


def _skip_id3v2(data: bytes) -> int:
    if len(data) >= 10 and data[:3] == b"ID3":
        size = (data[6] & 0x7F) << 21 | (data[7] & 0x7F) << 14 | (data[8] & 0x7F) << 7 | (data[9] & 0x7F)
        footer = 10 if data[5] & 0x10 else 0
        return 10 + size + footer
    return 0


def probe(data: bytes) -> Info:
    """Finds 3 chained Layer III frame headers after any ID3v2 tag. Raises Mp3Error otherwise."""
    start = _skip_id3v2(data)
    end = min(len(data), start + _SEARCH_WINDOW)
    seen_other_layer = seen_bad_rate = False
    for pos in range(start, end):
        first = _parse_header(data, pos)
        if first is None:
            continue
        if first == "layer":
            seen_other_layer = True
            continue
        if first == "rate":
            seen_bad_rate = True
            continue
        nxt, ok = pos, True
        for _ in range(_FRAMES_IN_SYNC):
            h = _parse_header(data, nxt)
            if not isinstance(h, _Header) or h.sample_rate != first.sample_rate or h.version != first.version:
                ok = False
                break
            nxt += h.length
        if ok:
            if first.sample_rate not in SUPPORTED_RATES:
                raise Mp3Error(f"{MSG_BAD_RATE}（{first.sample_rate} Hz）")
            return Info(first.sample_rate, first.channels)
    if seen_bad_rate:
        raise Mp3Error(MSG_BAD_RATE)
    if seen_other_layer:
        raise Mp3Error(MSG_NOT_LAYER3)
    raise Mp3Error(MSG_NOT_MP3)
