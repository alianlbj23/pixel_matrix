"""Converts a GIF into a PXG1 image for the board's `gif` partition (developer tool).

Used by menu.sh "Convert GIF"; the result (assets/gif.pxg) is flashed by `idf.py flash`.
PixelBoard Studio does the same thing with a GUI.
"""
import argparse
import logging
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "studio"))

from pixelboard import gifproc, pxg  # noqa: E402

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
        frames = gifproc.render_chain(src, gifproc.Placement(mode="split" if args.split else "whole"), args.chain)
        data = pxg.pack(frames, args.chain, args.brightness, args.swap)
    except gifproc.GifError as exc:
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
