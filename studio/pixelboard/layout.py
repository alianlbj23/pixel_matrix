"""Panel arrangement model: where each 64x32 HUB75 panel sits on the canvas.

Pure geometry (stdlib only). Panel-local coordinates (lx 0..63, ly 0..31) have ly=0 on the
panel's "up" side (the way the identify arrow points). rotation is the number of degrees the
panel is turned clockwise as seen from the front. Chain order is the panel index: the chain
buffer is 64*N x 32 with panel i occupying x = 64*i .. 64*i+63.
"""
import json
import logging
import math
import pathlib
from typing import NamedTuple, Optional

log = logging.getLogger(__name__)

PANEL_W = 64
PANEL_H = 32
MAX_PANELS = 8
ROTATIONS = (0, 90, 180, 270)
COORD_LIMIT = 4096
SNAP_THRESHOLD = 8
VERSION = 1


class LayoutError(Exception):
    """Invalid arrangement, with a plain-language message for the user."""

    def __init__(self, user_message: str):
        super().__init__(user_message)
        self.user_message = user_message


class Panel(NamedTuple):
    index: int
    x: int
    y: int
    rotation: int


class Arrangement(NamedTuple):
    panels: tuple

    @property
    def n(self) -> int:
        return len(self.panels)


def _make(panels) -> Arrangement:
    return Arrangement(tuple(sorted(panels, key=lambda p: p.index)))


def panel_size(rotation: int):
    return (PANEL_W, PANEL_H) if rotation in (0, 180) else (PANEL_H, PANEL_W)


def _rect(p: Panel):
    w, h = panel_size(p.rotation)
    return p.x, p.y, p.x + w, p.y + h


def _overlaps(a: Panel, b: Panel) -> bool:
    ax0, ay0, ax1, ay1 = _rect(a)
    bx0, by0, bx1, by1 = _rect(b)
    return ax0 < bx1 and bx0 < ax1 and ay0 < by1 and by0 < ay1


def validate(arr: Arrangement) -> None:
    panels = arr.panels
    if not 1 <= len(panels) <= MAX_PANELS:
        raise LayoutError(f"面板數量必須是 1 到 {MAX_PANELS} 塊")
    if sorted(p.index for p in panels) != list(range(len(panels))):
        raise LayoutError("面板編號必須是從 0 開始的連續數字,且不能重複")
    for p in panels:
        if p.rotation not in ROTATIONS:
            raise LayoutError("面板旋轉角度只能是 0、90、180 或 270 度")
        if not (-COORD_LIMIT <= p.x <= COORD_LIMIT and -COORD_LIMIT <= p.y <= COORD_LIMIT):
            raise LayoutError("面板位置超出可用範圍")
    for i, a in enumerate(panels):
        for b in panels[i + 1:]:
            if _overlaps(a, b):
                raise LayoutError("面板之間不能重疊")


def normalize(arr: Arrangement) -> Arrangement:
    if not arr.panels:
        raise LayoutError("至少需要一塊面板")
    dx = min(p.x for p in arr.panels)
    dy = min(p.y for p in arr.panels)
    return _make(p._replace(x=p.x - dx, y=p.y - dy) for p in arr.panels)


def bounds(arr: Arrangement):
    norm = normalize(arr)
    return (max(_rect(p)[2] for p in norm.panels), max(_rect(p)[3] for p in norm.panels))


def _best(current: int, candidates, threshold: int) -> int:
    near = [c for c in candidates if abs(c - current) <= threshold]
    return min(near, key=lambda c: abs(c - current)) if near else current


def snap(arr: Arrangement, index: int, x: int, y: int, threshold: int = SNAP_THRESHOLD) -> Arrangement:
    """Move panel `index` to (x, y), snapping edges to other panels within `threshold`."""
    moving = next((p for p in arr.panels if p.index == index), None)
    if moving is None:
        raise LayoutError("找不到這塊面板")
    mw, mh = panel_size(moving.rotation)
    xs, ys = [], []
    for o in arr.panels:
        if o.index == index:
            continue
        ox0, oy0, ox1, oy1 = _rect(o)
        # opposite edges touching, then parallel edges aligned
        xs += [ox1, ox0 - mw, ox0, ox1 - mw]
        ys += [oy1, oy0 - mh, oy0, oy1 - mh]
    moved = moving._replace(x=_best(x, xs, threshold), y=_best(y, ys, threshold))
    if any(_overlaps(moved, o) for o in arr.panels if o.index != index):
        return arr
    return _make([moved if p.index == index else p for p in arr.panels])


def resize(arr: Arrangement, n: int) -> Arrangement:
    if isinstance(n, bool) or not isinstance(n, int) or not 1 <= n <= MAX_PANELS:
        raise LayoutError(f"面板數量必須是 1 到 {MAX_PANELS} 塊")
    panels = list(arr.panels)
    if n < len(panels):
        return _make(panels[:n])
    right = max((_rect(p)[2] for p in panels), default=0)
    while len(panels) < n:
        p = Panel(len(panels), right, 0, 0)
        panels.append(p)
        right += PANEL_W
    return _make(panels)


def preset(name: str, n: int) -> Arrangement:
    if isinstance(n, bool) or not isinstance(n, int) or not 1 <= n <= MAX_PANELS:
        raise LayoutError(f"面板數量必須是 1 到 {MAX_PANELS} 塊")
    if name == "row":
        panels = [Panel(i, PANEL_W * i, 0, 0) for i in range(n)]
    elif name == "column":
        panels = [Panel(i, 0, PANEL_H * i, 0) for i in range(n)]
    elif name in ("grid", "snake"):
        cols = math.ceil(math.sqrt(n))
        panels = []
        for i in range(n):
            row, col = divmod(i, cols)
            if name == "snake" and row % 2 == 1:
                panels.append(Panel(i, PANEL_W * (cols - 1 - col), PANEL_H * row, 180))
            else:
                panels.append(Panel(i, PANEL_W * col, PANEL_H * row, 0))
    else:
        raise LayoutError("不認識的排列方式")
    return _make(panels)


def grid_shape(arr: Arrangement) -> Optional[tuple]:
    """(rows, cols, row-major panel index list) if panels form a full grid, else None."""
    panels = arr.panels
    if not panels or len({p.rotation for p in panels}) != 1:
        return None
    ys = sorted({p.y for p in panels})
    xs = sorted({p.x for p in panels})
    if len(ys) * len(xs) != len(panels):
        return None
    cells = {(p.y, p.x): p.index for p in panels}
    if len(cells) != len(panels):
        return None
    return len(ys), len(xs), [cells[(y, x)] for y in ys for x in xs]


def _local_to_canvas(p: Panel, lx: int, ly: int):
    r = p.rotation
    if r == 0:
        return p.x + lx, p.y + ly
    if r == 90:
        return p.x + 31 - ly, p.y + lx
    if r == 180:
        return p.x + 63 - lx, p.y + 31 - ly
    return p.x + ly, p.y + 63 - lx


def canvas_to_panel(p: Panel, cx: int, cy: int):
    """Panel-local (lx, ly) for canvas pixel (cx, cy), or None if outside the panel."""
    r = p.rotation
    dx, dy = cx - p.x, cy - p.y
    if r == 0:
        lx, ly = dx, dy
    elif r == 90:
        lx, ly = dy, 31 - dx
    elif r == 180:
        lx, ly = 63 - dx, 31 - dy
    else:
        lx, ly = 63 - dy, dx
    if 0 <= lx < PANEL_W and 0 <= ly < PANEL_H:
        return lx, ly
    return None


def chain_frame(canvas_rgb: bytes, cw: int, ch: int, arr: Arrangement) -> bytes:
    """Map an RGB888 canvas (already normalized size) to the 64*N x 32 chain buffer."""
    validate(arr)
    if len(canvas_rgb) != cw * ch * 3:
        raise LayoutError("畫面大小與排列不符")
    norm = normalize(arr)
    n = norm.n
    out = bytearray(PANEL_W * n * PANEL_H * 3)
    stride = PANEL_W * n
    for p in norm.panels:
        for ly in range(PANEL_H):
            for lx in range(PANEL_W):
                cx, cy = _local_to_canvas(p, lx, ly)
                if not (0 <= cx < cw and 0 <= cy < ch):
                    continue
                s = (cy * cw + cx) * 3
                d = (ly * stride + p.index * PANEL_W + lx) * 3
                out[d:d + 3] = canvas_rgb[s:s + 3]
    return bytes(out)


def canvas_frame(chain_rgb: bytes, arr: Arrangement):
    """Inverse of chain_frame: (canvas bytes, cw, ch); pixels outside every panel are black."""
    validate(arr)
    norm = normalize(arr)
    n = norm.n
    if len(chain_rgb) != PANEL_W * n * PANEL_H * 3:
        raise LayoutError("畫面大小與面板數量不符")
    cw, ch = bounds(norm)
    out = bytearray(cw * ch * 3)
    stride = PANEL_W * n
    for p in norm.panels:
        for ly in range(PANEL_H):
            for lx in range(PANEL_W):
                cx, cy = _local_to_canvas(p, lx, ly)
                s = (ly * stride + p.index * PANEL_W + lx) * 3
                d = (cy * cw + cx) * 3
                out[d:d + 3] = chain_rgb[s:s + 3]
    return bytes(out), cw, ch


def to_dict(arr: Arrangement) -> dict:
    return {
        "version": VERSION,
        "panels": [{"index": p.index, "x": p.x, "y": p.y, "rotation": p.rotation}
                   for p in arr.panels],
    }


def _is_int(v) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def from_dict(d) -> Arrangement:
    if not isinstance(d, dict) or not isinstance(d.get("panels"), list):
        raise LayoutError("排列資料格式不正確")
    panels = []
    for item in d["panels"]:
        if not isinstance(item, dict):
            raise LayoutError("排列資料格式不正確")
        vals = [item.get(k) for k in ("index", "x", "y", "rotation")]
        if not all(_is_int(v) for v in vals):
            raise LayoutError("排列資料格式不正確")
        panels.append(Panel(*vals))
    arr = _make(panels)
    validate(arr)
    return arr


def load(path) -> Arrangement:
    """Read an arrangement; never raises for bad content (falls back to one panel)."""
    path = pathlib.Path(path)
    try:
        with open(path, "r", encoding="utf-8") as f:
            return from_dict(json.load(f))
    except FileNotFoundError:
        log.warning("layout file %s not found, using default", path)
    except (OSError, ValueError, LayoutError) as e:
        log.warning("layout file %s unusable (%s), using default", path, e)
    return preset("row", 1)


def save(path, arr: Arrangement) -> None:
    validate(arr)
    path = pathlib.Path(path)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(to_dict(arr), f)
    except OSError:
        log.exception("could not save layout to %s", path)
        raise
