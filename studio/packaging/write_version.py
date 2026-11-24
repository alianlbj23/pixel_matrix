#!/usr/bin/env python3
"""Write pixelboard/_version.py: ``write_version.py <version> <out_path>``.

Stdlib only; runnable from any cwd. Imports pixelboard.version via sys.path
(rather than duplicating the regex) so validation has a single source of truth.
"""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from pixelboard.version import parse_version  # noqa: E402


def main(argv):
    if len(argv) != 3:
        print("usage: write_version.py <version> <out_path>", file=sys.stderr)
        return 2
    ver, out = argv[1], pathlib.Path(argv[2])
    if parse_version(ver) is None:
        print(f"invalid version: {ver!r} (expected X.Y.N)", file=sys.stderr)
        return 1
    out.write_text(f'APP_VERSION = "{ver}"\n', encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
