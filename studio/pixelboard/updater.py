"""Update check, verified download and per-platform install for PixelBoard Studio.

Flow: ``check()`` reads the GitHub "latest release" API and the release's ``latest.json``,
``download()`` streams the platform asset while hashing it, ``install()`` swaps the running
program for the new one and starts it.  All network and process I/O is injectable so the
tests never touch the network.  User-facing messages are Traditional Chinese (callers wrap
them as 「更新失敗：<原因>，你可以繼續使用目前的版本」); logs are English.
"""
import hashlib
import http.client
import json
import logging
import os
import platform
import posixpath
import re
import shlex
import shutil
import ssl
import stat
import subprocess
import sys
import tempfile
import threading
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path, PurePosixPath
from typing import Callable, Dict, Mapping, NamedTuple, Optional

from . import paths, version

log = logging.getLogger(__name__)

REPO = "alianlbj23/pixel_matrix"
API_LATEST_URL = f"https://api.github.com/repos/{REPO}/releases/latest"
RELEASES_URL = f"https://github.com/{REPO}/releases/latest"
DOWNLOAD_PREFIX = f"https://github.com/{REPO}/releases/download/"
MANIFEST_NAME = "latest.json"

API_TIMEOUT = 5.0
MANIFEST_TIMEOUT = 10.0
DOWNLOAD_TIMEOUT = 30.0          # per socket operation (each read), not for the whole download
MAX_SIZE = 600 * 1024 * 1024     # cap for any downloaded asset
MAX_JSON_BYTES = 4 * 1024 * 1024  # cap for API / latest.json responses
MAX_UNZIPPED = 2 * 1024 * 1024 * 1024
HELPER_WAIT_STEPS = 300          # macOS helper: 300 x 0.2 s = 60 s for the old process to exit
HELPER_LOG_NAME = "update-helper.log"  # macOS helper output, in ~/Library/Logs/PixelBoard-Studio/

MSG_CORRUPT = "下載的檔案不完整或已損壞"
MSG_NETWORK = "下載中斷，請確認網路連線後再試"
MSG_DISK = "無法寫入下載的檔案，請確認磁碟空間是否足夠"
MSG_BAD_INFO = "更新資訊格式不正確"
MSG_BAD_URL = "更新網址不安全，已停止更新"
MSG_UNSUPPORTED = "這個平台不支援自動更新"
MSG_NO_PERMISSION = "沒有權限更新程式所在的資料夾，已打開下載頁面，請手動下載新版"
MSG_NO_PERMISSION_NO_BROWSER = f"沒有權限更新程式所在的資料夾，請到 {RELEASES_URL} 手動下載新版"
MSG_INSTALL = "無法替換程式檔案"
MSG_RESTART = "無法自動重新開啟程式；新版本會在下次開啟時生效"
MSG_CANCELLED = "已取消更新"

_SHA_RE = re.compile(r"[0-9a-f]{64}")
_KEY_RE = re.compile(r"[a-z0-9_-]{1,32}")


class UpdateError(Exception):
    """`user_message` is shown to the user; `str(exc)` carries the English detail for logs."""

    def __init__(self, user_message: str, detail: Optional[str] = None):
        super().__init__(detail or user_message)
        self.user_message = user_message


class Asset(NamedTuple):
    name: str
    url: str
    size: int
    sha256: str


class Manifest(NamedTuple):
    version: str
    assets: Dict[str, Asset]


def platform_key(system: str = sys.platform, machine: str = platform.machine()) -> str:
    machine = (machine or "").lower()
    if system == "win32":
        return "windows-x64"
    if system == "darwin" and machine == "arm64":
        return "macos-arm64"
    if system == "darwin" and machine == "x86_64":
        return "macos-x86_64"
    if system.startswith("linux") and machine in ("x86_64", "amd64"):
        return "linux-x86_64"
    raise UpdateError(MSG_UNSUPPORTED, f"unsupported platform {system!r}/{machine!r}")


# --- latest.json / release API ---------------------------------------------------------

def _is_int(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _is_basename(name) -> bool:
    return (isinstance(name, str) and 0 < len(name) <= 255 and name not in (".", "..")
            and "/" not in name and "\\" not in name and "\0" not in name)


def _is_release_url(url) -> bool:
    """True only for plain https download URLs of this repo's releases (no '..', %-escapes, query, fragment)."""
    if not isinstance(url, str) or not url.startswith(DOWNLOAD_PREFIX):
        return False
    parts = urllib.parse.urlsplit(url)
    return not parts.query and not parts.fragment and ".." not in parts.path.split("/") \
        and "%" not in parts.path


def _bad_info(detail: str) -> UpdateError:
    return UpdateError(MSG_BAD_INFO, detail)


def parse_manifest(data: bytes, release_assets: Mapping[str, str]) -> Manifest:
    """Validates latest.json. Download URLs come only from `release_assets` (name -> url)."""
    try:
        doc = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, ValueError, AttributeError) as exc:
        raise _bad_info(f"latest.json is not valid JSON: {exc}") from exc
    if not isinstance(doc, dict):
        raise _bad_info("latest.json top level is not an object")
    ver = doc.get("version")
    if version.parse_version(ver) is None:
        raise _bad_info(f"latest.json has invalid version {ver!r}")
    entries = doc.get("assets")
    if not isinstance(entries, dict) or not entries:
        raise _bad_info("latest.json assets missing or empty")
    assets = {}
    for key, entry in entries.items():
        if not isinstance(key, str) or not _KEY_RE.fullmatch(key):
            raise _bad_info(f"latest.json has invalid platform key {key!r}")
        if not isinstance(entry, dict):
            raise _bad_info(f"latest.json asset {key} is not an object")
        name, size, sha = entry.get("name"), entry.get("size"), entry.get("sha256")
        if not _is_basename(name):
            raise _bad_info(f"latest.json asset {key} has invalid name {name!r}")
        if not _is_int(size) or not 1 <= size <= MAX_SIZE:
            raise _bad_info(f"latest.json asset {key} has invalid size {size!r}")
        if not isinstance(sha, str) or not _SHA_RE.fullmatch(sha):
            raise _bad_info(f"latest.json asset {key} has invalid sha256")
        url = release_assets.get(name)
        if url is None:
            raise _bad_info(f"latest.json asset {name!r} is not attached to the release")
        if not _is_release_url(url):
            raise _bad_info(f"asset {name!r} has untrusted url {url!r}")
        assets[key] = Asset(name, url, size, sha)
    return Manifest(ver, assets)


def _parse_release(data: bytes):
    """Returns (tag_name, {asset name: browser_download_url}) from the release API JSON."""
    try:
        doc = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, ValueError, AttributeError) as exc:
        raise _bad_info(f"release API response is not valid JSON: {exc}") from exc
    if not isinstance(doc, dict):
        raise _bad_info("release API response is not an object")
    tag, items = doc.get("tag_name"), doc.get("assets")
    if not isinstance(tag, str):
        raise _bad_info(f"release API tag_name invalid: {tag!r}")
    if not isinstance(items, list):
        raise _bad_info("release API assets is not a list")
    assets = {}
    for item in items:
        if not isinstance(item, dict):
            raise _bad_info("release API asset is not an object")
        name, url = item.get("name"), item.get("browser_download_url")
        if not isinstance(name, str) or not isinstance(url, str):
            raise _bad_info(f"release API asset has invalid name/url: {name!r} {url!r}")
        assets[name] = url
    return tag, assets


def check(current: str, http_get: Callable[[str, float], bytes], key: Optional[str] = None) -> Optional[Manifest]:
    """Returns the newer release's Manifest, or None (dev build, up to date, or any error - logged)."""
    if version.parse_version(current) is None:
        log.info("update check skipped: version %r is a dev build or not a release version", current)
        return None
    try:
        key = platform_key() if key is None else key
        tag, release_assets = _parse_release(http_get(API_LATEST_URL, API_TIMEOUT))
        manifest_url = release_assets.get(MANIFEST_NAME)
        if manifest_url is None:
            raise _bad_info(f"release {tag} has no {MANIFEST_NAME}")
        if not _is_release_url(manifest_url):
            raise _bad_info(f"untrusted {MANIFEST_NAME} url {manifest_url!r}")
        manifest = parse_manifest(http_get(manifest_url, MANIFEST_TIMEOUT), release_assets)
        if tag != "v" + manifest.version:
            raise _bad_info(f"release tag {tag!r} does not match latest.json version {manifest.version!r}")
    except (UpdateError, OSError, http.client.HTTPException, ValueError) as exc:
        log.warning("update check failed: %s", exc)
        return None
    except Exception:  # the check must never take the app down; record the full traceback
        log.exception("update check failed unexpectedly")
        return None
    if not version.is_newer(manifest.version, current):
        log.info("no update: latest %s, current %s", manifest.version, current)
        return None
    if key not in manifest.assets:
        log.info("update %s has no asset for %s", manifest.version, key)
        return None
    log.info("update available: %s (current %s)", manifest.version, current)
    return manifest


# --- HTTP -------------------------------------------------------------------------------

def _require_https(url) -> str:
    parts = urllib.parse.urlsplit(url) if isinstance(url, str) else None
    if parts is None or parts.scheme != "https" or not parts.hostname:
        raise UpdateError(MSG_BAD_URL, f"refusing non-https url {url!r}")
    return url


class _HttpsOnlyRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Follows redirects (GitHub -> objects.githubusercontent.com) only to https targets."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if urllib.parse.urlsplit(newurl).scheme != "https":
            raise urllib.error.HTTPError(newurl, code, f"refusing non-https redirect to {newurl}", headers, fp)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _request(url: str) -> urllib.request.Request:
    headers = {"User-Agent": f"PixelBoard-Studio/{version.current_version()}"}
    if urllib.parse.urlsplit(url).hostname == "api.github.com":
        headers["Accept"] = "application/vnd.github+json"
    return urllib.request.Request(url, headers=headers)


# System CA bundles, tried in order when the OpenSSL default paths do not exist (see _ssl_context).
MACOS_CA_BUNDLES = ("/etc/ssl/cert.pem",)
LINUX_CA_BUNDLES = ("/etc/ssl/certs/ca-certificates.crt",   # Debian/Ubuntu
                    "/etc/pki/tls/certs/ca-bundle.crt",     # Fedora/RHEL
                    "/etc/ssl/ca-bundle.pem",               # openSUSE
                    "/etc/ssl/cert.pem")                    # Arch, Alpine


def ca_bundle_candidates(system: str = sys.platform):
    if system == "darwin":
        return MACOS_CA_BUNDLES
    if system.startswith("linux"):
        return LINUX_CA_BUNDLES
    return ()   # Windows: create_default_context() reads the system certificate store


def _ssl_context(*, verify_paths: Callable = ssl.get_default_verify_paths, system: Optional[str] = None,
                 candidates=None) -> ssl.SSLContext:
    """A verifying TLS context that also works in frozen builds.

    A packaged app carries the build machine's OpenSSL OPENSSLDIR, which often does not exist on the
    user's machine (macOS, Fedora/RHEL/openSUSE/Arch): every https request would then fail with
    CERTIFICATE_VERIFY_FAILED. In that case the first existing system CA bundle is loaded instead.
    """
    ctx = ssl.create_default_context()
    system = sys.platform if system is None else system
    candidates = ca_bundle_candidates(system) if candidates is None else tuple(candidates)
    if not candidates:
        return ctx
    paths = verify_paths()
    cafile, capath = getattr(paths, "cafile", None), getattr(paths, "capath", None)
    if (cafile and os.path.exists(cafile)) or (capath and os.path.exists(capath)):
        return ctx
    for bundle in candidates:
        if not os.path.isfile(bundle):
            continue
        try:
            ctx.load_verify_locations(cafile=bundle)
        except (OSError, ssl.SSLError) as exc:
            log.warning("could not load CA bundle %s: %s", bundle, exc)
            continue
        log.info("OpenSSL default CA paths %r / %r missing; using %s", cafile, capath, bundle)
        return ctx
    log.error("no CA certificates found: OpenSSL default paths %r / %r and system bundles %s do not exist; "
              "https requests will fail certificate verification", cafile, capath, ", ".join(candidates))
    return ctx


def _open(url, timeout: float):
    _require_https(url)
    opener = urllib.request.build_opener(
        _HttpsOnlyRedirectHandler(), urllib.request.HTTPSHandler(context=_ssl_context()))
    return opener.open(_request(url), timeout=timeout)


def http_get(url: str, timeout: float) -> bytes:
    """GETs a small (<= MAX_JSON_BYTES) https response body."""
    with _open(url, timeout) as resp:
        data = resp.read(MAX_JSON_BYTES + 1)
    if len(data) > MAX_JSON_BYTES:
        raise _bad_info(f"response from {url} larger than {MAX_JSON_BYTES} bytes")
    return data


def open_stream(url: str, timeout: float):
    """Opens an https download; `timeout` applies to every socket read."""
    return _open(url, timeout)


# --- download ---------------------------------------------------------------------------

def _remove_file(path) -> None:
    try:
        os.unlink(path)
    except FileNotFoundError:
        pass
    except OSError as exc:
        log.warning("could not remove %s: %s", path, exc)


def _check_cancel(cancel, name: str) -> None:
    if cancel is not None and cancel.is_set():
        raise UpdateError(MSG_CANCELLED, f"download of {name} cancelled by the user")


def download(asset: Asset, dest_dir: Path, open_stream: Callable, progress: Callable[[float], None],
             chunk: int = 1 << 16, cancel: Optional[threading.Event] = None) -> Path:
    """Streams `asset` into dest_dir/<name>, verifying exact size and SHA-256.

    On any failure the partial file is deleted (an existing file of the same name is left
    alone) and UpdateError is raised: integrity problems -> MSG_CORRUPT, local file errors ->
    MSG_DISK, network errors -> MSG_NETWORK. `cancel` (set from another thread) is checked
    before connecting and between chunks -> MSG_CANCELLED.
    """
    if not _is_basename(asset.name) or not _is_int(asset.size) or not 1 <= asset.size <= MAX_SIZE \
            or not isinstance(asset.sha256, str) or not _SHA_RE.fullmatch(asset.sha256):
        raise _bad_info(f"invalid asset {asset!r}")
    _require_https(asset.url)
    dest_dir = Path(dest_dir)
    final = dest_dir / asset.name
    try:
        fd, tmp = tempfile.mkstemp(dir=dest_dir, prefix=".download-", suffix=".part")
    except OSError as exc:
        log.warning("cannot create download file in %s: %s", dest_dir, exc)
        raise UpdateError(MSG_DISK, f"cannot create download file in {dest_dir}: {exc}") from exc
    done = False
    try:
        digest = hashlib.sha256()
        total = 0
        progress(0.0)
        with os.fdopen(fd, "wb") as out:
            _check_cancel(cancel, asset.name)
            with open_stream(asset.url, DOWNLOAD_TIMEOUT) as stream:
                while True:
                    _check_cancel(cancel, asset.name)
                    block = stream.read(chunk)
                    if not block:
                        break
                    total += len(block)
                    if total > asset.size:
                        raise UpdateError(MSG_CORRUPT, f"{asset.name}: more than the declared {asset.size} bytes")
                    digest.update(block)
                    try:
                        out.write(block)
                    except OSError as exc:
                        raise UpdateError(MSG_DISK, f"writing {tmp} failed: {exc}") from exc
                    progress(total / asset.size)
        if total != asset.size:
            raise UpdateError(MSG_CORRUPT, f"{asset.name}: got {total} of {asset.size} bytes")
        if digest.hexdigest() != asset.sha256:
            raise UpdateError(MSG_CORRUPT, f"{asset.name}: SHA-256 mismatch")
        try:
            os.replace(tmp, final)
        except OSError as exc:
            raise UpdateError(MSG_DISK, f"moving download to {final} failed: {exc}") from exc
        done = True
        log.info("downloaded and verified %s (%d bytes)", asset.name, total)
        return final
    except UpdateError as exc:
        if exc.user_message == MSG_CANCELLED:
            log.info("download stopped: %s", exc)
        else:
            log.warning("download failed: %s", exc)
        raise
    except (OSError, http.client.HTTPException) as exc:
        log.warning("download of %s failed: %s", asset.name, exc)
        raise UpdateError(MSG_NETWORK, f"download of {asset.name} failed: {exc}") from exc
    finally:
        if not done:
            _remove_file(tmp)


# --- install ----------------------------------------------------------------------------

def _dir_writable(path: Path) -> bool:
    """os.access is unreliable on Windows (ACLs), so also create and delete a probe file."""
    if not os.access(path, os.W_OK):
        return False
    try:
        fd, probe = tempfile.mkstemp(dir=path, prefix=".pbs-write-test-")
    except OSError as exc:
        log.info("write probe in %s failed: %s", path, exc)
        return False
    os.close(fd)
    _remove_file(probe)
    return True


def install(downloaded: Path, key: str, exe_path: Path, *, spawn: Callable[[list], object],
            open_url: Callable[[str], object], pid: Optional[int] = None) -> None:
    """Replaces the program at exe_path with `downloaded` and starts the new one via `spawn`.

    The caller should quit right after this returns. On failure the old program is kept and
    UpdateError is raised. Without write permission the release page is opened instead.
    """
    downloaded, exe_path = Path(downloaded), Path(exe_path)
    target_dir = exe_path.parent
    if not _dir_writable(target_dir):
        log.warning("no write permission in %s; opening the release page", target_dir)
        try:
            open_url(RELEASES_URL)
        except Exception as exc:  # any browser failure: fall back to telling the user the URL
            log.warning("could not open %s: %s", RELEASES_URL, exc)
            raise UpdateError(MSG_NO_PERMISSION_NO_BROWSER, f"{target_dir} not writable") from exc
        raise UpdateError(MSG_NO_PERMISSION, f"{target_dir} not writable")
    try:
        if key == "windows-x64":
            install_windows(exe_path, downloaded, spawn)
        elif key == "linux-x86_64":
            install_appimage(exe_path, downloaded, spawn)
        elif key in ("macos-arm64", "macos-x86_64"):
            install_macos(exe_path, downloaded, spawn, os.getpid() if pid is None else pid)
        else:
            raise UpdateError(MSG_UNSUPPORTED, f"no installer for platform key {key!r}")
    except UpdateError as exc:
        log.warning("install failed: %s", exc)
        raise
    except OSError as exc:
        log.warning("install failed: %s", exc)
        raise UpdateError(MSG_INSTALL, f"install failed: {exc}") from exc


def install_windows(exe: Path, new: Path, spawn: Callable[[list], object]) -> None:
    """A running exe cannot be overwritten but can be renamed: exe -> exe.old, new -> exe."""
    old = exe.with_suffix(".exe.old")
    renamed = False
    try:
        if old.exists():
            old.unlink()            # leftover that cleanup_old could not remove last time
        os.replace(exe, old)
        renamed = True
        shutil.move(str(new), str(exe))
        spawn([str(exe)])
    except Exception as exc:
        log.warning("windows install failed (%s); rolling back", exc)
        if renamed:
            try:
                if exe.exists():
                    exe.unlink()
                os.replace(old, exe)
            except OSError:
                log.exception("rollback of %s failed; old version left at %s", exe, old)
        raise UpdateError(MSG_INSTALL, f"windows install failed: {exc}") from exc
    log.info("installed %s and started it", exe)


def install_appimage(appimage: Path, new: Path, spawn: Callable[[list], object]) -> None:
    """Copies to a temp file next to the AppImage, chmod 755, then atomically os.replace."""
    fd, tmp = tempfile.mkstemp(dir=appimage.parent, prefix=f".{appimage.name}.", suffix=".new")
    replaced = False
    try:
        with os.fdopen(fd, "wb") as out, open(new, "rb") as src:
            shutil.copyfileobj(src, out)
        os.chmod(tmp, 0o755)
        os.replace(tmp, appimage)
        replaced = True
    except OSError as exc:
        raise UpdateError(MSG_INSTALL, f"appimage install failed: {exc}") from exc
    finally:
        if not replaced:
            _remove_file(tmp)
    try:
        spawn([str(appimage)])
    except Exception as exc:
        # The file is already replaced (atomically); the running old version keeps working
        # and the next launch uses the new one.
        raise UpdateError(MSG_RESTART, f"could not start {appimage}: {exc}") from exc
    log.info("installed %s and started it", appimage)


def _validate_app_zip(zf: zipfile.ZipFile) -> str:
    """Returns the single top-level *.app name; rejects zip-slip, stray entries and escaping symlinks."""
    tops, symlinks, total = set(), set(), 0
    infos = [i for i in zf.infolist() if not i.filename.startswith("__MACOSX/")]
    for info in infos:
        name = info.filename
        parts = PurePosixPath(name).parts
        if not parts or name.startswith("/") or "\\" in name or ":" in parts[0] or "\0" in name \
                or any(p in ("..", ".") for p in parts):
            raise UpdateError(MSG_CORRUPT, f"unsafe zip member {name!r}")
        tops.add(parts[0])
        total += info.file_size
        if stat.S_ISLNK(info.external_attr >> 16):
            if info.file_size > 4096:
                raise UpdateError(MSG_CORRUPT, f"oversized symlink {name!r}")
            target = zf.read(info).decode("utf-8", "replace")
            resolved = posixpath.normpath(posixpath.join(posixpath.dirname(name.rstrip("/")), target))
            if target.startswith("/") or "\\" in target or not (resolved + "/").startswith(parts[0] + "/"):
                raise UpdateError(MSG_CORRUPT, f"symlink {name!r} -> {target!r} escapes the bundle")
            symlinks.add(name.rstrip("/"))
    if total > MAX_UNZIPPED:
        raise UpdateError(MSG_CORRUPT, f"zip expands to {total} bytes")
    if len(tops) != 1 or not next(iter(tops)).endswith(".app"):
        raise UpdateError(MSG_CORRUPT, f"zip must contain exactly one top-level .app, found {sorted(tops)}")
    for info in infos:
        parents = PurePosixPath(info.filename.rstrip("/")).parents
        if any(str(p) in symlinks for p in parents):
            raise UpdateError(MSG_CORRUPT, f"zip member {info.filename!r} lies under a symlink")
    return next(iter(tops))


def _extract_app_zip(zip_path: Path, dest: Path) -> None:
    """Extracts a validated zip keeping modes and symlinks (ditto on macOS, Python elsewhere)."""
    if sys.platform == "darwin" and os.path.exists("/usr/bin/ditto"):
        subprocess.run(["/usr/bin/ditto", "-x", "-k", str(zip_path), str(dest)],
                       check=True, timeout=300, stdin=subprocess.DEVNULL, capture_output=True)
        return
    with zipfile.ZipFile(zip_path) as zf:
        for info in zf.infolist():
            if info.filename.startswith("__MACOSX/"):
                continue
            target = dest.joinpath(*PurePosixPath(info.filename).parts)
            mode = info.external_attr >> 16
            if info.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            if stat.S_ISLNK(mode):
                os.symlink(zf.read(info).decode("utf-8"), target)
                continue
            with zf.open(info) as src, open(target, "wb") as out:
                shutil.copyfileobj(src, out)
            if stat.S_IMODE(mode):
                os.chmod(target, stat.S_IMODE(mode) & 0o755)


def _macos_helper_script(app: Path, new_app: Path, staging: Path, pid: int, *,
                         open_cmd: str = "/usr/bin/open", mv_cmd: str = "/bin/mv",
                         log_file: Optional[Path] = None, wait_steps: int = HELPER_WAIT_STEPS) -> str:
    """POSIX sh: wait for pid, swap bundles (old kept as .old until the new one opens), open.

    Every failure after the old process has exited opens an app again (the old one, restored when
    needed); only when the old process does not exit in time is everything left untouched. All output
    is appended to `log_file` (default ~/Library/Logs/PixelBoard-Studio/update-helper.log).
    """
    if not _is_int(pid) or pid <= 0:
        raise ValueError(f"invalid pid {pid!r}")
    if not _is_int(wait_steps) or wait_steps <= 0:
        raise ValueError(f"invalid wait_steps {wait_steps!r}")
    log_file = Path(log_file) if log_file is not None else paths.log_dir("darwin") / HELPER_LOG_NAME
    q = shlex.quote
    old = Path(str(app) + ".old")
    return f"""#!/bin/sh
# PixelBoard Studio update helper (generated). Replaces the app bundle after the old process exits.
PATH=/usr/bin:/bin:/usr/sbin:/sbin
export PATH
APP={q(str(app))}
NEW={q(str(new_app))}
OLD={q(str(old))}
STAGING={q(str(staging))}
OPEN={q(open_cmd)}
MV={q(mv_cmd)}
LOG_DIR={q(str(log_file.parent))}
LOG={q(str(log_file))}
mkdir -p "$LOG_DIR" 2>/dev/null
if ( : >> "$LOG" ) 2>/dev/null; then
  exec >> "$LOG" 2>&1
fi
log() {{
  echo "$(date '+%Y-%m-%d %H:%M:%S') update-helper[$$]: $*"
}}
open_any() {{
  for candidate in "$APP" "$OLD"; do
    if [ -d "$candidate" ]; then
      if "$OPEN" "$candidate"; then
        log "opened $candidate"
        return 0
      fi
      log "could not open $candidate"
    fi
  done
  log "no app could be opened"
  return 1
}}
log "waiting for pid {pid} to exit before replacing $APP"
waited=0
while kill -0 {pid} 2>/dev/null; do
  waited=$((waited + 1))
  if [ "$waited" -gt {wait_steps} ]; then
    log "pid {pid} still running after {wait_steps} checks; leaving $APP untouched"
    rm -rf "$STAGING"
    exit 1
  fi
  sleep 0.2
done
rm -rf "$OLD"
if [ -e "$OLD" ] || [ -L "$OLD" ]; then
  log "cannot remove the stale $OLD; keeping the current version"
  rm -rf "$STAGING"
  open_any
  exit 1
fi
if ! "$MV" "$APP" "$OLD"; then
  log "cannot move $APP aside; keeping the current version"
  rm -rf "$STAGING"
  open_any
  exit 1
fi
if "$MV" "$NEW" "$APP"; then
  if "$OPEN" "$APP"; then
    log "updated $APP and opened it"
    rm -rf "$OLD"
    rm -rf "$STAGING"
    exit 0
  fi
  log "could not open the new version; restoring the previous one"
else
  log "cannot move the new version into place; restoring the previous one"
fi
rm -rf "$APP"
if "$MV" "$OLD" "$APP"; then
  log "restored the previous version to $APP"
else
  log "restoring $OLD to $APP failed"
fi
open_any
rm -rf "$STAGING"
exit 1
"""


def install_macos(app_bundle: Path, new_zip: Path, spawn: Callable[[list], object], pid: int) -> None:
    """Stages the new .app next to the old one and hands the swap to a detached sh helper."""
    try:
        with zipfile.ZipFile(new_zip) as zf:
            app_name = _validate_app_zip(zf)
    except zipfile.BadZipFile as exc:
        raise UpdateError(MSG_CORRUPT, f"{new_zip} is not a valid zip: {exc}") from exc
    staging = Path(tempfile.mkdtemp(dir=app_bundle.parent, prefix=".PixelBoard-Studio-update-"))
    try:
        _extract_app_zip(new_zip, staging)
        entries = [p.name for p in staging.iterdir() if p.name != "__MACOSX"]
        new_app = staging / app_name
        if entries != [app_name] or not new_app.is_dir() or new_app.is_symlink():
            raise UpdateError(MSG_CORRUPT, f"unexpected extracted content {entries}")
        helper = staging / "update.sh"
        helper.write_text(_macos_helper_script(app_bundle, new_app, staging, pid), encoding="utf-8")
        spawn(["/bin/sh", str(helper)])
    except Exception as exc:
        shutil.rmtree(staging, ignore_errors=True)
        if isinstance(exc, UpdateError):
            raise
        raise UpdateError(MSG_INSTALL, f"macOS install failed: {exc}") from exc
    log.info("staged %s; helper will replace %s after pid %d exits", new_app, app_bundle, pid)


# --- process helpers --------------------------------------------------------------------

def cleanup_old(exe_path: Optional[Path]) -> None:
    """Removes the `<exe>.old` file (Windows) or `<App>.app.old` bundle (macOS) left by an update."""
    if exe_path is None:
        return
    old = Path(exe_path).with_name(Path(exe_path).name + ".old")
    try:
        if old.is_dir() and not old.is_symlink():
            shutil.rmtree(old)
        elif old.exists() or old.is_symlink():
            os.unlink(old)
        else:
            return
        log.info("removed leftover %s", old)
    except OSError as exc:
        log.warning("could not remove leftover %s: %s", old, exc)


def current_executable(*, frozen: Optional[bool] = None, system: Optional[str] = None,
                       executable: Optional[str] = None, env: Optional[Mapping] = None) -> Optional[Path]:
    """The file/bundle an update replaces, or None when not running a packaged build."""
    frozen = getattr(sys, "frozen", False) if frozen is None else frozen
    if not frozen:
        return None
    system = sys.platform if system is None else system
    executable = sys.executable if executable is None else executable
    env = os.environ if env is None else env
    if system == "win32":
        return Path(executable)
    if system == "darwin":
        for parent in Path(executable).parents:
            if parent.suffix == ".app":
                return parent
        return None
    appimage = (env.get("APPIMAGE") or "").strip()
    return Path(appimage) if appimage else None


def _child_env() -> dict:
    env = dict(os.environ)
    env["PYINSTALLER_RESET_ENVIRONMENT"] = "1"   # new onefile instance must unpack itself again
    if getattr(sys, "frozen", False):            # undo the bootloader's LD_LIBRARY_PATH change
        orig = env.get("LD_LIBRARY_PATH_ORIG")
        if orig is not None:
            env["LD_LIBRARY_PATH"] = orig
        else:
            env.pop("LD_LIBRARY_PATH", None)
    return env


def spawn_detached(argv: list) -> None:
    """Starts argv fully detached so it outlives this process (no shell)."""
    if not isinstance(argv, list) or not argv or not all(isinstance(a, str) for a in argv):
        raise TypeError("argv must be a non-empty list of str")
    kwargs = {"env": _child_env(), "stdin": subprocess.DEVNULL, "stdout": subprocess.DEVNULL,
              "stderr": subprocess.DEVNULL, "close_fds": True}
    if os.name == "nt":
        kwargs["creationflags"] = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        kwargs["start_new_session"] = True
    subprocess.Popen(argv, **kwargs)
    log.info("spawned %s", argv)
