"""Converts a GIF into a PXG1 image for the board's `gif` partition (developer tool).

Used by menu.sh "Convert GIF"; the result (assets/gif.pxg) is flashed by `idf.py flash`.
PixelBoard Studio does the same thing with a GUI.
"""
import argparse
import logging
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "studio"))

from pixelboard import gifproc, layout, pxg  # noqa: E402

log = logging.getLogger("pack_gif")


def main(argv=None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(name)s: %(message)s")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", help="source .gif")
    parser.add_argument("output", help="output .pxg")
    parser.add_argument("--chain", type=int, default=1, help="chained 64x32 panels (1 or 2)")
    parser.add_argument("--split", action="store_true", help="one part of the GIF per panel")
    parser.add_argument("--brightness", type=int, default=90, help="panel brightness 10..128")
    parser.add_argument("--swap", action="store_true", help="panels are cabled right-to-left")
    args = parser.parse_args(argv)

    try:
        src = gifproc.load(args.input)
        if args.chain not in (1, 2):
            raise gifproc.GifError(f"面板數只能是 1 或 2（目前是 {args.chain}）")
        if args.split and args.chain != 2:
            raise gifproc.GifError("「每塊面板一份」需要 2 塊串接的面板")
        placement = gifproc.Placement(mode="split" if args.split else "whole")
        arr = layout.legacy_row(args.chain, args.swap)
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
    log.info("wrote %s: %d frame(s), chain %d, %d bytes", out, len(frames), args.chain, len(data))
    return 0


if __name__ == "__main__":
    sys.exit(main())
