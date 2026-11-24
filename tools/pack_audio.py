"""Packs assets/audio/*.mp3 into a PXA1 image for the board's `audio` partition (developer tool).

Runs from the ESP-IDF build with IDF's Python, so it uses only the standard library.
Files are played in file-name order. Invalid MP3s fail the build with a reason.
"""
import argparse
import logging
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "studio"))

from pixelboard import pxa  # noqa: E402  (stdlib-only module)

log = logging.getLogger("pack_audio")


def main(argv=None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(name)s: %(message)s")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", help="folder with .mp3 files")
    parser.add_argument("output", help="output .pxa")
    args = parser.parse_args(argv)

    folder = pathlib.Path(args.directory)
    if not folder.is_dir():
        log.error("not a directory: %s", folder)
        return 1
    files = sorted((p for p in folder.iterdir() if p.is_file() and p.suffix.lower() == ".mp3"),
                   key=lambda p: p.name)
    tracks = []
    for path in files:
        try:
            data = path.read_bytes()
            info = pxa.validate_track(path.name, data)
        except OSError as exc:
            log.error("cannot read %s: %s", path, exc)
            return 1
        except pxa.PxaError as exc:
            log.error("%s: %s", path.name, exc.user_message)
            return 1
        log.info("%s: %d Hz, %d ch, %d bytes", path.name, info.sample_rate, info.channels, len(data))
        tracks.append(pxa.Track(path.name, data))
    try:
        image = pxa.pack(tracks)
    except pxa.PxaError as exc:
        log.error("%s", exc.user_message)
        return 1
    out = pathlib.Path(args.output)
    try:
        out.write_bytes(image)
    except OSError as exc:
        log.error("cannot write %s: %s", out, exc)
        return 1
    log.info("wrote %s: %d track(s), %d bytes", out, len(tracks), len(image))
    return 0


if __name__ == "__main__":
    sys.exit(main())
