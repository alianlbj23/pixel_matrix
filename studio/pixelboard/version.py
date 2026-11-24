"""App version handling.

Release builds: CI generates ``pixelboard/_version.py`` (``APP_VERSION = "X.Y.N"``)
via ``packaging/write_version.py`` and it is bundled by PyInstaller.
Source/dev builds: version is ``<studio/VERSION>.0-dev`` and never checks updates.
Frozen without ``_version.py``: studio/VERSION is not shipped, so fall back to
``FALLBACK_DEV_VERSION`` instead of crashing.
"""
import pathlib
import re
import sys

_VERSION_RE = re.compile(r"\d{1,6}\.\d{1,6}\.\d{1,9}")
_SERIES_RE = re.compile(r"\d+\.\d+")
FALLBACK_DEV_VERSION = "0.0.0-dev"


def parse_version(s):
    """Return (major, minor, build) for strictly formatted 'X.Y.N', else None."""
    if not isinstance(s, str) or not _VERSION_RE.fullmatch(s):
        return None
    major, minor, build = s.split(".")
    return int(major), int(minor), int(build)


def is_newer(candidate, current):
    a, b = parse_version(candidate), parse_version(current)
    if a is None or b is None:
        return False
    return a > b


def _read_series():
    path = pathlib.Path(__file__).resolve().parent.parent / "VERSION"
    text = path.read_text(encoding="utf-8").strip()
    if not _SERIES_RE.fullmatch(text):
        raise ValueError(f"invalid studio/VERSION content: {text!r}")
    return text


def current_version():
    try:
        from pixelboard import _version
    except ImportError:
        _version = None
    if _version is not None:
        return _version.APP_VERSION
    if getattr(sys, "frozen", False):
        return FALLBACK_DEV_VERSION
    return _read_series() + ".0-dev"


def is_dev_build():
    return current_version().endswith("-dev")
