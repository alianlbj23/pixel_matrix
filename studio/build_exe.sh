#!/usr/bin/env bash
# Builds dist/PixelBoard-Studio.exe from WSL using the Windows python.org Python 3.10.
# Needs a finished `idf.py build` (firmware images are bundled into the exe).
set -euo pipefail

readonly REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
readonly FW_FILES=(bootloader/bootloader.bin partition_table/partition-table.bin pixel-board.bin flasher_args.json)

die() { echo "build_exe: $*" >&2; exit 1; }

command -v cmd.exe >/dev/null || die "cmd.exe not found: this script must run in WSL"
WIN_LOCALAPPDATA="$(cmd.exe /c "echo %LOCALAPPDATA%" 2>/dev/null | tr -d '\r')"
[[ "$WIN_LOCALAPPDATA" == [A-Za-z]:\\* ]] || die "cannot read %LOCALAPPDATA% (got '$WIN_LOCALAPPDATA')"
readonly WORK="$(wslpath "$WIN_LOCALAPPDATA")/pixelboard-studio-build"
readonly PY310="$(wslpath "$WIN_LOCALAPPDATA")/Programs/Python/Python310/python.exe"
[[ -x "$PY310" ]] || die "Windows Python 3.10 not found at $PY310 (install it from python.org)"

for f in "${FW_FILES[@]}"; do
    [[ -f "$REPO/build/$f" ]] || die "missing build/$f: run Build first"
done

# A fresh staging dir per build: an exe still running from an older build keeps its files
# locked on Windows, so old dirs are removed best-effort only.
readonly SRC="$WORK/src-$(date +%Y%m%d-%H%M%S)"
for old in "$WORK"/src "$WORK"/src-*; do
    [[ -d "$old" ]] || continue
    rm -rf "$old" 2>/dev/null || echo "    (skipped $(basename "$old"): still in use)"
done
echo "==> Staging sources in $SRC"
mkdir -p "$SRC/firmware/bootloader" "$SRC/firmware/partition_table"
cp -r "$REPO/studio/pixelboard" "$REPO/studio/run_studio.py" "$REPO/studio/PixelBoard-Studio.spec" \
      "$REPO/studio/requirements.txt" "$SRC/"
for f in "${FW_FILES[@]}"; do cp "$REPO/build/$f" "$SRC/firmware/$f"; done
find "$SRC" -name '__pycache__' -prune -exec rm -rf {} +

readonly VENV_PY="$WORK/venv/Scripts/python.exe"
if [[ ! -x "$VENV_PY" ]]; then
    echo "==> Creating Windows venv"
    "$PY310" -m venv "$(wslpath -w "$WORK/venv")"
fi
echo "==> Installing pinned requirements"
"$VENV_PY" -m pip install --disable-pip-version-check -q -r "$(wslpath -w "$SRC/requirements.txt")"
"$VENV_PY" -m pip freeze --disable-pip-version-check | tr -d '\r' > "$REPO/studio/requirements-lock.txt"

echo "==> Running PyInstaller"
(cd "$SRC" && "$VENV_PY" -m PyInstaller --noconfirm --log-level WARN \
    --distpath dist --workpath work PixelBoard-Studio.spec)

mkdir -p "$REPO/dist"
cp "$SRC/dist/PixelBoard-Studio.exe" "$REPO/dist/PixelBoard-Studio.exe" ||
    die "cannot replace dist/PixelBoard-Studio.exe (close PixelBoard Studio first)"
echo "==> Built $REPO/dist/PixelBoard-Studio.exe ($(du -h "$REPO/dist/PixelBoard-Studio.exe" | cut -f1))"
echo "    Windows path: $(wslpath -w "$REPO/dist/PixelBoard-Studio.exe")"
