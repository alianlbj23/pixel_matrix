#!/usr/bin/env python3
"""Write latest.json and SHA256SUMS for a release: ``make_manifest.py <version> <assets_dir> <out_dir>``.

<assets_dir> must hold the four platform builds named
``PixelBoard-Studio-<version>-<key><ext>`` (other files are ignored). latest.json has the format that
``pixelboard.updater.parse_manifest`` accepts; SHA256SUMS is ``sha256sum`` output ("<hex>  <name>").
Exits 1 (writing nothing) if the version is not "X.Y.N" or any platform build is missing, empty or
larger than the updater accepts; exits 2 on bad usage.

Stdlib only; runnable from any cwd. Imports pixelboard.version via sys.path so the version rule has a
single source of truth (as packaging/write_version.py does).
"""
import hashlib
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from pixelboard.version import parse_version  # noqa: E402

# Platform key -> file extension; keys match pixelboard.updater.platform_key().
PLATFORMS = {
    "windows-x64": ".exe",
    "macos-arm64": ".zip",
    "macos-x86_64": ".zip",
    "linux-x86_64": ".AppImage",
}
MAX_SIZE = 600 * 1024 * 1024  # same cap as pixelboard.updater.MAX_SIZE
CHUNK = 1024 * 1024


def asset_name(ver: str, key: str) -> str:
    return f"PixelBoard-Studio-{ver}-{key}{PLATFORMS[key]}"


def _sha256(path: pathlib.Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(CHUNK), b""):
            digest.update(block)
    return digest.hexdigest()


def collect(ver: str, assets_dir: pathlib.Path) -> dict:
    """Returns {key: {"name", "size", "sha256"}}; raises ValueError naming every problem found."""
    entries, problems = {}, []
    for key in PLATFORMS:
        name = asset_name(ver, key)
        path = assets_dir / name
        if not path.is_file():
            problems.append(f"missing {key} build: {name}")
            continue
        size = path.stat().st_size
        if not 1 <= size <= MAX_SIZE:
            problems.append(f"{name} has size {size}, expected 1..{MAX_SIZE} bytes")
            continue
        entries[key] = {"name": name, "size": size, "sha256": _sha256(path)}
    if problems:
        raise ValueError("; ".join(problems))
    return entries


def main(argv) -> int:
    if len(argv) != 4:
        print("usage: make_manifest.py <version> <assets_dir> <out_dir>", file=sys.stderr)
        return 2
    ver, assets_dir, out_dir = argv[1], pathlib.Path(argv[2]), pathlib.Path(argv[3])
    if parse_version(ver) is None:
        print(f"make_manifest: invalid version {ver!r} (expected X.Y.N)", file=sys.stderr)
        return 1
    if not assets_dir.is_dir():
        print(f"make_manifest: assets dir not found: {assets_dir}", file=sys.stderr)
        return 1
    try:
        entries = collect(ver, assets_dir)
    except (ValueError, OSError) as exc:
        print(f"make_manifest: {exc}", file=sys.stderr)
        return 1
    manifest = {"version": ver, "assets": entries}
    sums = "".join(f"{e['sha256']}  {e['name']}\n" for e in entries.values())
    try:
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "latest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
        (out_dir / "SHA256SUMS").write_text(sums, encoding="utf-8", newline="\n")
    except OSError as exc:
        print(f"make_manifest: cannot write {out_dir}: {exc}", file=sys.stderr)
        return 1
    for e in entries.values():
        print(f"{e['name']}  {e['size']} bytes  sha256 {e['sha256']}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
