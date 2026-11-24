#!/usr/bin/env bash
# Turns the PyInstaller onedir (dist/PixelBoard-Studio/) into an AppImage.
#   build_appimage.sh <onedir> <version> <out.AppImage> <appimagetool>
# AppDir layout: usr/bin/<onedir contents>, AppRun, pixelboard-studio.desktop, pixelboard-studio.png
# (name = Icon= in the .desktop file) and .DirIcon. This script downloads nothing; the caller passes
# pinned, SHA-256-verified tools (verified with them on 2026-11-25):
#   appimagetool 1.9.1  https://github.com/AppImage/appimagetool/releases/download/1.9.1/appimagetool-x86_64.AppImage
#     sha256 ed4ce84f0d9caff66f50bcca6ff6f35aae54ce8135408b3fa33abfc3cb384eb0
#   type2 runtime 20251108  https://github.com/AppImage/type2-runtime/releases/download/20251108/runtime-x86_64
#     sha256 2fca8b443c92510f1483a883f60061ad09b46b978b2631c807cd873a47ec260d
# APPIMAGE_RUNTIME_FILE (optional): that runtime, passed to appimagetool as --runtime-file. Without it
# appimagetool downloads the *unpinned* "continuous" runtime, so CI must set it.
set -euo pipefail

readonly HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
readonly APP_NAME="PixelBoard-Studio"
readonly DESKTOP="$HERE/pixelboard-studio.desktop"
readonly ICON="$HERE/icon.png"

die() { echo "build_appimage: $*" >&2; exit 1; }

[[ $# -eq 4 ]] || die "usage: build_appimage.sh <onedir> <version> <out.AppImage> <appimagetool>"
readonly ONEDIR="$1" APP_VERSION="$2" OUT="$3" TOOL="$4"

[[ -d "$ONEDIR" ]] || die "onedir not found: $ONEDIR"
[[ -x "$ONEDIR/$APP_NAME" ]] || die "missing executable $ONEDIR/$APP_NAME (run pyinstaller first)"
[[ "$APP_VERSION" =~ ^[0-9]{1,6}\.[0-9]{1,6}\.[0-9]{1,9}(-dev)?$ ]] || die "invalid version: '$APP_VERSION' (expected X.Y.N or X.Y.N-dev)"
[[ "$OUT" == *.AppImage ]] || die "output must end in .AppImage: $OUT"
[[ -f "$TOOL" && -x "$TOOL" ]] || die "appimagetool not found or not executable: $TOOL"
[[ -f "$DESKTOP" && -f "$ICON" ]] || die "missing $DESKTOP or $ICON"
TOOL_ARGS=()
if [[ -n "${APPIMAGE_RUNTIME_FILE:-}" ]]; then
    [[ -f "$APPIMAGE_RUNTIME_FILE" ]] || die "APPIMAGE_RUNTIME_FILE not found: $APPIMAGE_RUNTIME_FILE"
    TOOL_ARGS+=(--runtime-file "$APPIMAGE_RUNTIME_FILE")
else
    echo "build_appimage: warning: APPIMAGE_RUNTIME_FILE not set, appimagetool downloads an unpinned runtime" >&2
fi

ICON_NAME="$(sed -n 's/^Icon=//p' "$DESKTOP")"
[[ "$ICON_NAME" =~ ^[A-Za-z0-9._-]+$ ]] || die "bad Icon= entry in $DESKTOP: '$ICON_NAME'"

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
readonly APPDIR="$WORK/$APP_NAME.AppDir"

mkdir -p "$APPDIR/usr/bin"
cp -a "$ONEDIR/." "$APPDIR/usr/bin/"
cat > "$APPDIR/AppRun" <<'APPRUN'
#!/bin/sh
HERE="$(dirname "$(readlink -f "$0")")"
exec "$HERE/usr/bin/PixelBoard-Studio" "$@"
APPRUN
chmod 755 "$APPDIR/AppRun"
cp "$DESKTOP" "$APPDIR/"
cp "$ICON" "$APPDIR/$ICON_NAME.png"
ln -s "$ICON_NAME.png" "$APPDIR/.DirIcon"

mkdir -p "$(dirname "$OUT")"
rm -f "$OUT"
ARCH=x86_64 VERSION="$APP_VERSION" "$TOOL" "${TOOL_ARGS[@]}" "$APPDIR" "$OUT"
[[ -f "$OUT" ]] || die "appimagetool did not produce $OUT"
chmod 755 "$OUT"
echo "build_appimage: built $OUT"
