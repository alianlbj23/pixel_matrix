#!/usr/bin/env python3
"""Release guard: ``check_release_version.py <new_version> <latest_tag>``.

<new_version> is the "X.Y.N" about to be released; <latest_tag> is the tag of the current latest
GitHub release ("vX.Y.N"; a plain "X.Y.N" is accepted too) or "" when there is no release yet.
Exits 0 when the new version is strictly newer (or there is no release), 1 when it is not newer or
either value is invalid (a release must never take "latest" from a newer one: installed apps would
not update to it, and a lower version would hide the real newest build), 2 on bad usage.

Stdlib only; runnable from any cwd. Imports pixelboard.version via sys.path so the version rule has a
single source of truth (as make_manifest.py does).
"""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from pixelboard.version import parse_version  # noqa: E402


def main(argv) -> int:
    if len(argv) != 3:
        print("usage: check_release_version.py <new_version> <latest_tag or empty>", file=sys.stderr)
        return 2
    new, latest = argv[1], argv[2]
    new_v = parse_version(new)
    if new_v is None:
        print(f"check_release_version: invalid new version {new!r} (expected X.Y.N)", file=sys.stderr)
        return 1
    if latest == "":
        print(f"No release yet: {new} will be the first one")
        return 0
    latest_ver = latest[1:] if latest.startswith("v") else latest
    latest_v = parse_version(latest_ver)
    if latest_v is None:
        print(f"check_release_version: invalid latest release tag {latest!r} (expected vX.Y.N)", file=sys.stderr)
        return 1
    if new_v <= latest_v:
        print(f"check_release_version: {new} is not newer than the latest release {latest}", file=sys.stderr)
        return 1
    print(f"{new} is newer than the latest release {latest}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
