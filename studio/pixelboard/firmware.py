"""Locating and loading the pixel-board firmware images bundled with the app."""
import json
import logging
import pathlib
import sys

from .board import BoardError

log = logging.getLogger(__name__)

# Flash offsets of the images the app writes on install / firmware update.
IMAGE_OFFSETS = {"bootloader": 0x0, "partition_table": 0x8000, "app": 0x10000}
MSG_MISSING = "程式內附的韌體檔案遺失或損壞，請重新下載 PixelBoard Studio"


def bundled_dir() -> pathlib.Path:
    """`firmware/` inside the PyInstaller bundle, or the repo's ESP-IDF `build/` when run from source."""
    if getattr(sys, "frozen", False):
        return pathlib.Path(sys._MEIPASS) / "firmware"  # type: ignore[attr-defined]
    return pathlib.Path(__file__).resolve().parents[2] / "build"


def load_images(base: pathlib.Path = None) -> dict:
    """Returns {"bootloader", "partition_table", "app"} bytes, as listed in flasher_args.json."""
    base = base or bundled_dir()
    try:
        args = json.loads((base / "flasher_args.json").read_text(encoding="utf-8"))
        by_offset = {int(off, 16): rel for off, rel in args["flash_files"].items()}
        images = {}
        for name, offset in IMAGE_OFFSETS.items():
            if offset not in by_offset:
                raise ValueError(f"flasher_args.json has no image at 0x{offset:x} for {name}")
            images[name] = (base / by_offset[offset]).read_bytes()
    except (OSError, ValueError, KeyError, TypeError) as exc:
        log.error("cannot load firmware images from %s: %s", base, exc)
        raise BoardError(MSG_MISSING) from exc
    return images
