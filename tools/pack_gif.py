"""Converts a GIF into a PXG1 image for the board's `gif` partition (developer tool).

Used by menu.sh "Convert GIF"; the result (assets/gif.pxg) is flashed by `idf.py flash`.
PixelBoard Studio does the same thing with a GUI.
"""
import argparse
import json
import logging
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "studio"))

from pixelboard import gifproc, layout, pxg  # noqa: E402

log = logging.getLogger("pack_gif")


def load_layout(path: str) -> layout.Arrangement:
    """Strict load: unlike layout.load, a bad file is an error, never a silent fallback."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            return layout.from_dict(json.load(f))
    except (OSError, ValueError) as exc:
        raise layout.LayoutError(f"無法讀取排列檔 {path}：{exc}") from exc


def main(argv=None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(name)s: %(message)s")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", help="source .gif")
    parser.add_argument("output", help="output .pxg")
    parser.add_argument("--layout", help="arrangement JSON (Studio's layout.json); overrides --chain")
    parser.add_argument("--chain", type=int, default=1, help="panels in a row, 1..8 (ignored with --layout)")
    parser.add_argument("--split", action="store_true", help="one part of the GIF per panel")
    parser.add_argument("--brightness", type=int, default=90, help="panel brightness 10..128")
    args = parser.parse_args(argv)

    try:
        src = gifproc.load(args.input)
        if args.layout:
            arr = load_layout(args.layout)
        else:
            if not 1 <= args.chain <= layout.MAX_PANELS:
                raise gifproc.GifError(f"面板數要在 1 到 {layout.MAX_PANELS} 之間（目前是 {args.chain}）")
            arr = layout.preset("row", args.chain)
        placement = gifproc.Placement(mode="split" if args.split else "whole")
        w, h = layout.bounds(arr)
        frames = [(layout.chain_frame(rgb, w, h, arr), ms) for rgb, ms in gifproc.render(src, placement, arr)]
        data = pxg.pack(frames, arr, args.brightness, args.split)
    except (gifproc.GifError, layout.LayoutError) as exc:
        log.error("%s", exc.user_message)
        return 1
    except pxg.PxgError as exc:
        log.error("%s", exc)
        return 1
    out = pathlib.Path(args.output)
    try:
        out.write_bytes(data)
    except OSError as exc:
        log.error("cannot write %s: %s", out, exc)
        return 1
    log.info("wrote %s: %d frame(s), chain %d, %d bytes", out, len(frames), arr.n, len(data))
    return 0


if __name__ == "__main__":
    sys.exit(main())
