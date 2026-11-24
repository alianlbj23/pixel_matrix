"""GIF decoding and layout onto 1 or 2 chained 64x32 HUB75 panels (RGB888 frames).

The bytes returned by render() are exactly what gets packed into the board's gif
partition, so the app preview and the panel show the same pixels.
"""
import logging
import pathlib
from dataclasses import dataclass
from typing import Optional

from PIL import Image, ImageSequence, UnidentifiedImageError

log = logging.getLogger(__name__)

PANEL_W = 64
PANEL_H = 32
MAX_FRAMES = 64
MAX_INPUT_BYTES = 10 * 1024 * 1024
DEFAULT_DURATION_MS = 100
MIN_DURATION_MS = 20
MAX_DURATION_MS = 10000
# Frames are downsized to this longest side on load: the largest render (128 px wide at
# scale 4) needs far less, and it bounds memory for huge GIFs (64 x 512^2 x 4 B = 64 MB).
MAX_WORK_SIDE = 512
MIN_SCALE, MAX_SCALE = 0.25, 4.0
MAX_OFFSET = 128

_RESAMPLE = Image.Resampling.LANCZOS


class GifError(Exception):
    """Invalid GIF or layout, with a plain-language message for the user."""

    def __init__(self, user_message: str):
        super().__init__(user_message)
        self.user_message = user_message


@dataclass(frozen=True)
class Layout:
    chain: int = 1
    split: bool = False
    offset_x: int = 0
    offset_y: int = 0
    scale: float = 1.0

    def validate(self) -> None:
        if self.chain not in (1, 2):
            raise GifError(f"面板數只能是 1 或 2（目前是 {self.chain}）")
        if self.split and self.chain != 2:
            raise GifError("「每塊面板一份」需要 2 塊串接的面板")
        if not isinstance(self.scale, (int, float)) or not MIN_SCALE <= self.scale <= MAX_SCALE:
            raise GifError(f"縮放倍數要在 {MIN_SCALE}–{MAX_SCALE} 之間")
        for name, value in (("水平位置", self.offset_x), ("垂直位置", self.offset_y)):
            if not isinstance(value, int) or not -MAX_OFFSET <= value <= MAX_OFFSET:
                raise GifError(f"{name}要在 -{MAX_OFFSET}–{MAX_OFFSET} 之間")


@dataclass
class SourceGif:
    name: str
    frames: list            # composited RGBA PIL images, longest side <= MAX_WORK_SIDE
    durations: list         # ms per frame, already clamped
    visible_box: Optional[tuple]  # union of non-transparent areas over all frames


def clamp_duration(value) -> int:
    if not isinstance(value, (int, float)) or value <= 0:
        return DEFAULT_DURATION_MS
    return int(min(max(value, MIN_DURATION_MS), MAX_DURATION_MS))


def _shrink(rgba):
    longest = max(rgba.size)
    if longest <= MAX_WORK_SIDE:
        return rgba
    f = MAX_WORK_SIDE / longest
    return rgba.resize((max(1, round(rgba.width * f)), max(1, round(rgba.height * f))), _RESAMPLE)


def load(path) -> SourceGif:
    """Decodes and composites every frame once. Raises GifError on bad input."""
    path = pathlib.Path(path)
    if not path.is_file():
        raise GifError(f"找不到檔案：{path.name}")
    size = path.stat().st_size
    if size > MAX_INPUT_BYTES:
        raise GifError(f"檔案太大（{size / 1048576:.1f}MB），上限是 10MB")
    try:
        with Image.open(path) as im:
            if im.format != "GIF":
                raise GifError(f"「{path.name}」不是 GIF 動畫檔")
            n_frames = getattr(im, "n_frames", 1)
            if n_frames > MAX_FRAMES:
                raise GifError(f"這個 GIF 有 {n_frames} 格，超過上限 {MAX_FRAMES} 格，請縮短")
            frames, durations = [], []
            for frame in ImageSequence.Iterator(im):
                frames.append(_shrink(frame.convert("RGBA")))
                durations.append(clamp_duration(frame.info.get("duration")))
    except GifError:
        raise
    except (UnidentifiedImageError, Image.DecompressionBombError) as exc:
        log.warning("cannot decode %s: %s", path, exc)
        raise GifError(f"「{path.name}」不是 GIF 動畫檔，或檔案已損壞") from exc
    except OSError as exc:
        log.warning("cannot read %s: %s", path, exc)
        raise GifError(f"無法讀取「{path.name}」：{exc}") from exc
    if not frames:
        raise GifError(f"「{path.name}」裡沒有任何畫面")
    boxes = [b for b in (f.getchannel("A").getbbox() for f in frames) if b]
    box = None
    if boxes:
        box = (min(b[0] for b in boxes), min(b[1] for b in boxes),
               max(b[2] for b in boxes), max(b[3] for b in boxes))
    return SourceGif(path.name, frames, durations, box)


def _paste(canvas, img, scale, cell_w, cell_h, layout):
    """Scales img and pastes it centred in a cell_w x cell_h canvas, shifted by the layout offset."""
    w = max(1, round(img.width * scale))
    h = max(1, round(img.height * scale))
    scaled = img.resize((w, h), _RESAMPLE)
    x = (cell_w - w) // 2 + layout.offset_x
    y = (cell_h - h) // 2 + layout.offset_y
    canvas.paste(scaled, (x, y), scaled)  # PIL clips to the canvas, so nothing leaks out


def render(src: SourceGif, layout: Layout) -> list:
    """Returns [(rgb888_bytes, duration_ms)] for a (64*chain) x 32 canvas."""
    layout.validate()
    width = PANEL_W * layout.chain
    out = []
    if not layout.split:
        for rgba, ms in zip(src.frames, src.durations):
            canvas = Image.new("RGB", (width, PANEL_H), (0, 0, 0))
            fit = min(width / rgba.width, PANEL_H / rgba.height)
            _paste(canvas, rgba, fit * layout.scale, width, PANEL_H, layout)
            out.append((canvas.tobytes(), ms))
        return out

    if src.visible_box is None:
        raise GifError("這個 GIF 是全透明的，沒有可以顯示的內容")
    left, top, right, bottom = src.visible_box
    if right - left < layout.chain:
        raise GifError("GIF 的可見範圍太窄，無法分給每塊面板")
    edges = [left + round(i * (right - left) / layout.chain) for i in range(layout.chain + 1)]
    widest = max(edges[i + 1] - edges[i] for i in range(layout.chain))
    fit = min(PANEL_W / widest, PANEL_H / (bottom - top))  # same scale for every panel
    for rgba, ms in zip(src.frames, src.durations):
        canvas = Image.new("RGB", (width, PANEL_H), (0, 0, 0))
        for i in range(layout.chain):
            panel = Image.new("RGB", (PANEL_W, PANEL_H), (0, 0, 0))
            strip = rgba.crop((edges[i], top, edges[i + 1], bottom))
            _paste(panel, strip, fit * layout.scale, PANEL_W, PANEL_H, layout)
            canvas.paste(panel, (i * PANEL_W, 0))
        out.append((canvas.tobytes(), ms))
    return out
