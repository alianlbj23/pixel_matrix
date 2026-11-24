"""Per-platform locations for PixelBoard-Studio's data, logs and layout."""
import os
import sys
from pathlib import Path
from typing import Mapping

APP_DIR = "PixelBoard-Studio"


def data_dir(platform: str = sys.platform, env: Mapping = os.environ, home: Path = None) -> Path:
    home = Path.home() if home is None else home
    if platform == "win32":
        base = (env.get("LOCALAPPDATA") or "").strip()
        return (Path(base) if base else home / "AppData" / "Local") / APP_DIR
    if platform == "darwin":
        return home / "Library" / "Application Support" / APP_DIR
    xdg = (env.get("XDG_DATA_HOME") or "").strip()
    return (Path(xdg) if xdg else home / ".local" / "share") / APP_DIR


def log_dir(platform: str = sys.platform, env: Mapping = os.environ, home: Path = None) -> Path:
    if platform == "darwin":
        return (Path.home() if home is None else home) / "Library" / "Logs" / APP_DIR
    return data_dir(platform, env, home)


def layout_path(platform: str = sys.platform, env: Mapping = os.environ, home: Path = None) -> Path:
    return data_dir(platform, env, home) / "layout.json"
