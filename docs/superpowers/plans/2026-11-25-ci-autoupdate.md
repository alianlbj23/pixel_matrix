# CI Release and Auto-Update Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 推上 `main` 時，GitHub Actions 自動測試、build 韌體、打包 Windows／macOS（arm64 與 Intel）／Linux 三平台的 PixelBoard Studio 並發布 Release；程式開啟時檢查新版，詢問後自動下載、驗證、替換並重新開啟（燒錄中禁止）。

**Architecture:**
- **版本**：版本號由 `studio/VERSION` 加上 CI 的 run number 組成，打包時寫入 `pixelboard/_version.py`。
- **更新邏輯**：`pixelboard/updater.py` 是純 Python（標準函式庫 urllib），負責下載清單、驗證、各平台的檔案替換。網路 I/O 可以注入，方便離線測試。
- **CI**：`release.yml` 依序執行 test → firmware（ESP-IDF 容器，可重現建置）→ package（四個 runner）→ release（gh CLI）。
- **清單**：`latest.json` 由 `studio/ci/make_manifest.py` 產生，記錄每個平台的 SHA-256。

**Tech Stack:** GitHub Actions、PyInstaller 6.22.3、pywebview 6.2.1（Windows edgechromium、macOS cocoa、Linux qt）、PySide6、qtpy、pyobjc、appimagetool、ESP-IDF v5.5、Python 標準函式庫（urllib、hashlib、zipfile、json）、unittest。

**Spec:** `docs/superpowers/specs/2026-11-24-ci-autoupdate-design.md`

## Global Constraints

- **分支**：在 `feature/studio` 上工作，每個 task 都要 commit。**不准 push，也不准合併到 main**。Task 7 會先停下來詢問使用者。
- **相依套件**：只能新增使用者核准的套件，全部固定版本。
  - Linux：`PySide6`、`qtpy`
  - macOS：`pyobjc-core`、`pyobjc-framework-Cocoa`、`pyobjc-framework-WebKit`
  - CI 用：`appimagetool`（固定版本並驗證 SHA-256）、`actions/checkout`、`actions/setup-python`、`actions/upload-artifact`、`actions/download-artifact`，GitHub actions 一律用 **commit SHA** 固定
  - 不使用其他第三方 action。Release 用 runner 內建的 `gh` 建立。
- **版本號**：
  - 格式：`<studio/VERSION 內容>.<GITHUB_RUN_NUMBER>`，例如 `1.2.57`。
  - 本機開發版：`<VERSION>.0-dev`。開發版不檢查更新。
  - `studio/VERSION` 的內容是 `1.2`。
- **韌體**：
  - `PROJECT_VER` 改成 `"1.2.0"`，之後跟 app 版本各自獨立。
  - 開啟 `CONFIG_APP_REPRODUCIBLE_BUILD=y`。
  - CI 要 build 兩次，比對 `pixel-board.bin` 的 SHA-256，不同就讓 CI 失敗。
- **release 觸發**：`push` 到 `main`，搭配 `paths` 允許清單：
  - `studio/pixelboard/**`
  - `studio/run_studio.py`
  - `studio/PixelBoard-Studio.spec`
  - `studio/requirements*.txt`
  - `studio/VERSION`
  - `studio/packaging/**`
  - `studio/ci/**`
  - `main/**`
  - `CMakeLists.txt`
  - `sdkconfig`
  - `sdkconfig.defaults`
  - `partitions.csv`
  - `dependencies.lock`
  - `.github/workflows/release.yml`

  另外加上 `workflow_dispatch`，並且讓 `push` 到 `ci-test/**` 分支時以 **dry-run** 執行：只打包、不發布。
- **ci.yml**：每次 push 和每個 pull_request 都跑測試，不發布。
- **成品檔名**：
  - `PixelBoard-Studio-<ver>-windows-x64.exe`
  - `PixelBoard-Studio-<ver>-macos-arm64.zip`
  - `PixelBoard-Studio-<ver>-macos-x86_64.zip`
  - `PixelBoard-Studio-<ver>-linux-x86_64.AppImage`
  - 另外產出 `latest.json` 和 `SHA256SUMS`
- **平台代號**：`windows-x64`、`macos-arm64`、`macos-x86_64`、`linux-x86_64`
- **更新檢查**：
  - 先 `GET https://api.github.com/repos/alianlbj23/pixel_matrix/releases/latest`，timeout 5 秒。
  - 再讀取該 release 附的 `latest.json` asset，timeout 10 秒。
  - User-Agent 用 `PixelBoard-Studio/<ver>`，**不能帶任何個人資訊**。
  - 失敗時只寫 log，不打擾使用者。
- **下載**：
  - 只能用 HTTPS，每次 read 都要有 timeout。
  - 檔案大小要等於清單記錄的大小，而且不能超過 600 MiB。
  - SHA-256 要跟清單一致，不一致就刪掉檔案、不安裝。
- **更新和燒錄互鎖**：
  - 燒錄中不能開始更新，提示延到燒錄結束後才出現。
  - 更新中不能燒錄。
- **更新失敗時**：保留舊版，並顯示「更新失敗：<原因>，你可以繼續使用目前的版本」。
- **沒有寫入權限時**：打開 `https://github.com/alianlbj23/pixel_matrix/releases/latest`，並說明原因。
- **使用者看得到的文字**用繁體中文；程式碼和 log 用英文。不准吞例外；I/O 要有 timeout；外部輸入要驗證，包括 API 回應、`latest.json` 和下載的檔案。
- **完成的定義**：
  - `menu.sh` 的 do_tests 全部 OK
  - `idf.py build` 沒有新的 warning
  - Windows 本機的 `studio/build_exe.sh` 仍然能產出 exe
- **測試指令**：`.venv-studio/bin/python -m unittest discover -s studio/tests -t studio`

## Review Focus

1. **沒有網路，或 GitHub API 回 403（被限流）、回 HTML 或回傳格式錯誤**：預期不跳出任何錯誤，只寫 log，程式照常使用。由 Task 3 的 `test_check_offline_silent`、`test_check_bad_json_silent` 負責。
2. **下載不完整、大小不符或 SHA-256 不符**：預期不安裝，舊版維持原狀，並顯示原因。由 Task 3 的 `test_download_sha_mismatch_keeps_old`、`test_download_truncated` 負責。
3. **程式放在沒有寫入權限的資料夾**：預期改成打開 Releases 頁面並說明。由 Task 3 的 `test_install_readonly_opens_release_page` 負責。
4. **版本字串不合規格**，例如 `v1.2`、`1.2.57-dev`、`1.2`、空字串、`1.2.x`：預期不視為新版。由 Task 1 的 `test_parse_version_rejects` 和 Task 3 的 `test_check_ignores_bad_version` 負責。
5. **燒錄中檢查到新版**，或在更新下載中按燒錄：預期提示延後出現、燒錄被拒絕。由 Task 4 的 `test_update_deferred_while_flashing`、`test_flash_rejected_while_updating` 負責。

---

## File Structure

```
studio/VERSION                        新增：大版本號 "1.2"
studio/pixelboard/version.py          新增：current_version、parse_version、is_newer
studio/pixelboard/paths.py            新增：各平台的資料夾和 log 路徑
studio/pixelboard/updater.py          新增：檢查、下載、驗證、各平台替換、重新開啟
studio/pixelboard/app.py              修改：check_update、start_update、update 互鎖、各平台 GUI
studio/pixelboard/flasher.py          修改：Linux 序列埠權限錯誤的對照訊息
studio/pixelboard/ui/*                修改：更新對話框和下載進度
studio/run_studio.py                  修改：路徑改用 paths、各平台對話框、啟動時清掉 .old
studio/PixelBoard-Studio.spec         修改：依平台輸出 onefile / BUNDLE / onedir
studio/packaging/build_appimage.sh    新增：onedir 打包成 AppImage（AppRun、.desktop、圖示）
studio/packaging/write_version.py     新增：產生 pixelboard/_version.py
studio/ci/make_manifest.py            新增：從成品資料夾產生 latest.json 和 SHA256SUMS
studio/requirements.txt               修改：各平台共用的直接相依套件
studio/requirements-macos.txt         新增：pyobjc 系列（固定版本）
studio/requirements-linux.txt         新增：PySide6、qtpy（固定版本）
.github/workflows/ci.yml              新增
.github/workflows/release.yml         新增
CMakeLists.txt、sdkconfig、sdkconfig.defaults   修改：版本 1.2.0、可重現建置
README.md                             修改：各平台安裝、放行方式、自動更新說明
```

---

### Task 1: 版本模組和韌體可重現建置

**Files:**
- Create: `studio/VERSION`、`studio/pixelboard/version.py`、`studio/packaging/write_version.py`、`studio/tests/test_version.py`
- Modify: `CMakeLists.txt`（PROJECT_VER "1.2.0"）、`sdkconfig.defaults`、`sdkconfig`（`CONFIG_APP_REPRODUCIBLE_BUILD=y`）、`.gitignore`（加入 `studio/pixelboard/_version.py`）

**Interfaces:**
- Produces:
  - `version.parse_version(s: str) -> tuple[int, int, int] | None`：只接受 `^\d{1,6}\.\d{1,6}\.\d{1,9}$`
  - `version.is_newer(candidate: str, current: str) -> bool`：任何一邊無法解析時回傳 False
  - `version.current_version() -> str`：
    - 有 `pixelboard/_version.py` 時，回傳其中的 `APP_VERSION`
    - 否則回傳 `<VERSION>.0-dev`
  - `version.is_dev_build() -> bool`
  - `write_version.py <version> <out_path>`：寫出 `APP_VERSION = "<version>"`，寫入前先用 `parse_version` 驗證版本號

- [ ] **Step 1: 寫失敗測試**
  - `test_parse_version_ok`：`"1.2.57"` 解析成 `(1, 2, 57)`
  - `test_parse_version_rejects`：以下都回傳 None
    - `"v1.2.3"`、`"1.2"`、`"1.2.57-dev"`、`""`、`"1.2.x"`、`"1.2.3.4"`、`" 1.2.3"`
  - `test_is_newer`：
    - `("1.2.58", "1.2.57")` → True
    - `("1.10.0", "1.9.9")` → True
    - `("1.2.57", "1.2.57")` → False
    - `("garbage", "1.2.57")` → False
    - `("1.3.0", "1.2.0-dev")` → False（開發版不比較）
  - `test_current_version_dev`：沒有 `_version.py` 時，結果結尾是 `.0-dev`，前綴等於 `studio/VERSION` 的內容
  - `test_write_version_validates`：寫入不合法的版本時 exit code 是 1，而且不產生檔案
- [ ] **Step 2:** 執行測試，預期 FAIL。
- [ ] **Step 3:** 實作。修改韌體設定後，連續 build 兩次，確認 SHA-256 一致：
  ```
  idf build
  sha256sum build/pixel-board.bin
  idf fullclean && idf build
  sha256sum build/pixel-board.bin
  ```
  兩次的 SHA-256 要相同。不同的話，把差異寫進報告，並查清楚原因（例如路徑沒有被重新映射）。
- [ ] **Step 4:** do_tests 全部 OK。
- [ ] **Step 5: 實機驗證**：用 StudioApi 走 firmware_only，預期是 `update_firmware`，因為版本變成 1.2.0。開機 log 要顯示 `App version: 1.2.0`，眼睛動畫正常。
- [ ] **Step 6: Commit**：`feat: app version module and reproducible firmware build 1.2.0`

---

### Task 2: 跨平台執行環境

**Files:**
- Create: `studio/pixelboard/paths.py`、`studio/tests/test_paths.py`
- Modify: `studio/run_studio.py`、`studio/pixelboard/app.py`（layout 路徑改用 paths；`run()` 依平台選 GUI）、`studio/pixelboard/flasher.py`（`to_board_error`）、`studio/tests/test_board_ports.py`

**Interfaces:**
- Produces:
  - `paths.data_dir(platform: str = sys.platform, env: Mapping = os.environ, home: Path = Path.home()) -> Path`，各平台的位置：
    - `win32`：`%LOCALAPPDATA%\PixelBoard-Studio`。沒有 LOCALAPPDATA 時用 `home/AppData/Local/PixelBoard-Studio`。
    - `darwin`：`home/Library/Application Support/PixelBoard-Studio`
    - 其他：`$XDG_DATA_HOME/PixelBoard-Studio`。沒有 XDG_DATA_HOME 時用 `home/.local/share/PixelBoard-Studio`。
  - `paths.log_dir(...)`：
    - darwin 是 `home/Library/Logs/PixelBoard-Studio`
    - 其他平台等於 `data_dir`
  - `paths.layout_path(...)`：`data_dir / "layout.json"`
  - `app.gui_for(platform) -> str`：
    - `win32` → `"edgechromium"`
    - `darwin` → `"cocoa"`
    - 其他 → `"qt"`
  - `flasher.MSG_NO_PERMISSION = "沒有權限使用 USB 序列埠：請執行 sudo usermod -aG dialout $USER 後重新登入"`
    - 只要是 Linux 上的 `Permission denied` 或 `[Errno 13]`，而且 port 是 `/dev/tty*`，就對照成這個訊息
    - Windows 的 `PermissionError(13, 'Access is denied')` 維持原本的「被其他程式占用」

- [ ] **Step 1: 寫失敗測試**
  - `test_paths_windows`、`test_paths_macos`、`test_paths_linux_xdg`、`test_paths_linux_default`
  - `test_gui_for`
  - `test_linux_permission_message`：`SerialException("[Errno 13] could not open port /dev/ttyACM0: [Errno 13] Permission denied: '/dev/ttyACM0'")` 要對照成 `MSG_NO_PERMISSION`
  - `test_windows_access_denied_still_busy`
- [ ] **Step 2:** 執行測試，預期 FAIL。
- [ ] **Step 3:** 實作。`run_studio.py` 的調整：
  - 對話框：
    - Windows 用 `MessageBoxW`
    - macOS 用 `osascript -e 'display alert ...'`，參數用 list 傳入，**不能用 shell**
    - Linux 用 stderr，有 Qt 的話用 `QMessageBox`
  - WebView2 的提示只在 Windows 顯示。
- [ ] **Step 4:** 全部測試 PASS。
- [ ] **Step 5: Commit**：`feat(studio): cross-platform paths, GUI backend and serial permission message`

---

### Task 3: 更新模組 `updater.py`

**Files:**
- Create: `studio/pixelboard/updater.py`、`studio/tests/test_updater.py`

**Interfaces:**
- Consumes：Task 1 的 `version.parse_version`、`is_newer`、`current_version`
- Produces：
  - `updater.UpdateError(Exception)`：有 `.user_message` 屬性
  - `updater.platform_key(system=sys.platform, machine=platform.machine()) -> str`：
    - win32 → `windows-x64`
    - darwin 加上 arm64 → `macos-arm64`
    - darwin 加上 x86_64 → `macos-x86_64`
    - linux 加上 x86_64 → `linux-x86_64`
    - 其他 → 丟出 `UpdateError`
  - `Asset = NamedTuple(name: str, url: str, size: int, sha256: str)`
  - `Manifest = NamedTuple(version: str, assets: dict[str, Asset])`
  - `updater.parse_manifest(data: bytes, release_assets: dict[str, str]) -> Manifest`，格式和檢查規則：
    - `data` 是 `latest.json`，格式是 `{"version": "1.2.57", "assets": {"windows-x64": {"name": "...", "size": 123, "sha256": "<64 hex>"}, ...}}`
    - 下載網址不能寫在 JSON 裡，一律從 `release_assets`（`name` 對 `browser_download_url`）取得
    - 網址必須以 `https://github.com/alianlbj23/pixel_matrix/releases/download/` 開頭
    - 每個欄位的型別和範圍都要驗證：size 在 1 到 600 MiB 之間；sha256 是 64 個小寫 hex
  - `updater.check(current: str, http_get: Callable[[str, float], bytes]) -> Manifest | None`：
    - 開發版直接回傳 None
    - 任何錯誤都記 log 並回傳 None，不丟出例外
    - 只有 `is_newer` 成立，而且清單裡有本平台的 asset 時，才回傳 Manifest
  - `updater.download(asset: Asset, dest_dir: Path, open_stream, progress: Callable[[float], None], chunk=1 << 16) -> Path`：
    - 一邊下載一邊計算 SHA-256
    - 超過宣告的大小、少於宣告的大小、hash 不符時：刪掉檔案，並丟出 `UpdateError`，訊息含「下載的檔案不完整或已損壞」
  - `updater.install(downloaded: Path, key: str, exe_path: Path, *, spawn, open_url) -> None`：
    - 依 key 呼叫對應的平台處理函式。
    - 目標資料夾不能寫入時，呼叫 `open_url(RELEASES_URL)`，並丟出 `UpdateError`，訊息含「沒有權限更新」。
    - 三種平台的處理方式：
      - `install_windows(exe, new, spawn)`：
        1. 先把 `exe` 改名成 `exe.with_suffix(".exe.old")`
        2. 再把 `new` 移動到 `exe`
        3. 執行 `spawn([str(exe)])`
        4. 失敗時把改名還原
      - `install_appimage(appimage, new, spawn)`：
        1. 把 `new` 複製到同一個資料夾的暫存檔
        2. `chmod 0o755`
        3. `os.replace` 換掉原檔
        4. 執行 `spawn([str(appimage)])`
      - `install_macos(app_bundle, new_zip, spawn, pid)`：
        1. 把 zip 解到暫存位置，裡面必須剛好只有一個 `*.app`
        2. 寫出 helper sh：等 `pid` 結束 → `mv` 舊的 app 成 `.old` → `mv` 新的 app 到原位置 → `open` 新的 app → 刪掉 `.old`；任何一步失敗就把 `.old` 還原
        3. 執行 `spawn(["/bin/sh", helper])`，參數全部用 list 傳入
  - `updater.cleanup_old(exe_path: Path) -> None`：刪掉留下來的 `.exe.old` 或 `.old`。刪不掉時只記 log。
  - `updater.current_executable() -> Path | None`：
    - 不是 frozen 的話回傳 None
    - Linux 回傳 `$APPIMAGE`
    - macOS 回傳 `.app` bundle 的根目錄
    - Windows 回傳 `sys.executable`
  - `RELEASES_URL = "https://github.com/alianlbj23/pixel_matrix/releases/latest"`
  - 真的 HTTP 請求放在 `updater.http_get(url, timeout)` 和 `updater.open_stream(url, timeout)`，用 urllib 實作，帶 User-Agent，並且只接受 https 網址

- [ ] **Step 1: 寫失敗測試**，全部用 fake 的 http 和暫存資料夾，不碰網路：
  - `test_platform_key_all`、`test_platform_key_unknown_raises`
  - `test_parse_manifest_ok`
  - `test_parse_manifest_rejects`：
    - 缺少欄位
    - size 是 0 或超過 600 MiB
    - sha 不是 64 個 hex
    - 網址不是本 repo
    - JSON 壞掉
  - `test_check_returns_newer`、`test_check_same_version_none`、`test_check_ignores_bad_version`、`test_check_dev_build_none`
  - `test_check_offline_silent`：`http_get` 丟出 `URLError`，結果是 None，而且有 log
  - `test_check_bad_json_silent`：API 回 HTML 時結果是 None
  - `test_check_missing_platform_none`
  - `test_download_ok_progress`：進度要遞增，最後到 1.0
  - `test_download_sha_mismatch_keeps_old`、`test_download_truncated`、`test_download_oversize`
  - `test_install_windows_swaps_and_spawns`、`test_install_windows_rollback_on_failure`
  - `test_install_appimage_replaces_mode_755`
  - `test_install_macos_writes_helper`：helper 的內容要包含等待 pid、`mv` 和 `open`；`spawn` 的參數是 list
  - `test_install_readonly_opens_release_page`：在 POSIX 上把資料夾設成 `chmod 0o555`，在 Windows 上用 mock
  - `test_cleanup_old`
- [ ] **Step 2:** 執行測試，預期 FAIL。
- [ ] **Step 3:** 實作。
- [ ] **Step 4:** 測試 PASS。
- [ ] **Step 5: Commit**：`feat(studio): updater (check, verified download, per-platform install)`

---

### Task 4: StudioApi 和 UI 整合更新

**Files:**
- Modify: `studio/pixelboard/app.py`、`studio/pixelboard/ui/index.html`、`app.js`、`style.css`、`studio/run_studio.py`（啟動時呼叫 `updater.cleanup_old`）、`studio/tests/test_api.py`

**Interfaces:**
- Consumes：Task 3 的整個 updater
- Produces，StudioApi 新增的方法，全部回傳 dict：
  - `StudioApi.__init__` 新增可注入的參數：`updater_check`、`updater_download`、`updater_install`、`current_version`
  - `check_update()`：在背景執行緒檢查一次。有新版時回傳 `{"ok": True, "available": bool, "version": str, "current": str, "has_work": bool}`。`has_work` 表示目前已經加入 GIF 或音樂。
  - `start_update()`：
    - 燒錄中回傳 `_fail("燒錄中不能更新，請等燒錄結束")`
    - 開始下載後，`_updating` 設成 True，期間 `prepare_flash` 和 `confirm_flash` 一律回傳 `_fail("正在更新程式，請稍候")`
    - 下載進度透過 `window.studio.onUpdateProgress(fraction)` 回報
    - 成功時在 `install` 之後呼叫 `self._quit()`，讓舊版結束
    - 失敗時回傳 `{"ok": False, "message": "更新失敗：<原因>，你可以繼續使用目前的版本"}`，並把 `_updating` 設回 False
  - `get_update_state()`：讓 UI 在燒錄結束後查詢是否有延後的提示
- **UI**：
  - 開啟程式後呼叫 `check_update()`。
  - 有新版時：
    - 燒錄中：先記下來，等燒錄結束再顯示。
    - 不在燒錄中：顯示對話框「目前有新版本 X（你的版本 Y），是否更新？」，按鈕是「更新」和「稍後」。
    - `has_work` 為 true 時，對話框加一行：「更新會重新開啟程式，目前加入的 GIF 和音樂需要重新加入（面板排列會保留）」。
  - 下載時顯示進度條，所有 `.lockable` 都鎖住。

- [ ] **Step 1: 寫失敗測試**
  - `test_check_update_available`
  - `test_update_deferred_while_flashing`：燒錄中呼叫 `start_update` 被拒絕；`check_update` 仍然回報 available
  - `test_flash_rejected_while_updating`
  - `test_update_failure_message_and_unlock`
  - `test_update_success_quits`：`install` 被呼叫，`_quit` 被呼叫
  - `test_has_work_flag`
- [ ] **Step 2:** 執行測試，預期 FAIL。
- [ ] **Step 3:** 實作，用 `node --check` 檢查 JS，並交叉比對 id。
- [ ] **Step 4:** 測試 PASS。
- [ ] **Step 5: Commit**：`feat(studio): update prompt, download progress and flash interlock`

---

### Task 5: 各平台打包

**Files:**
- Create: `studio/packaging/build_appimage.sh`、`studio/packaging/pixelboard-studio.desktop`、`studio/packaging/icon.png`（256x256，簡單的 LED 點陣圖示，用 Pillow 產生後 commit）、`studio/requirements-macos.txt`、`studio/requirements-linux.txt`
- Modify: `studio/PixelBoard-Studio.spec`、`studio/requirements.txt`、`studio/build_exe.sh`（先呼叫 `write_version.py` 寫入 `<VERSION>.0-dev`，再打包）

**規格：**
- **spec 依 `sys.platform` 決定輸出：**
  - win32：跟現在一樣，onefile，`console=False`。
  - darwin：`EXE(..., exclude_binaries=True)`，再用 `COLLECT` 和 `BUNDLE`：
    - 名稱 `PixelBoard Studio.app`
    - `bundle_identifier="io.github.alianlbj23.pixelboard-studio"`
    - `info_plist` 寫入 `CFBundleShortVersionString` 等於 APP_VERSION，`LSMinimumSystemVersion="11.0"`
  - linux：onedir（`COLLECT`），輸出到 `dist/PixelBoard-Studio/`。
  - 所有平台都要包含 `pixelboard/ui` 和 `firmware/`。
- **`build_appimage.sh <onedir> <version> <out.AppImage> <appimagetool>`：**
  - 建立 AppDir，內容是 `usr/bin/<onedir 的內容>`、`AppRun`、`.desktop` 和圖示。
  - 用 appimagetool 打包。
  - 全程 `set -euo pipefail`，參數要驗證。
- **requirements 版本：**
  - 版本要在 2026-11-11 之前發布：PyPI 上的上傳日期至少比今天（2026-11-25）早兩週。
  - 實作時用 PyPI JSON API 查詢後填入確切版本。
  - **不准挑兩週內才發布的版本。**

- [ ] **Step 1:** 寫 `studio/tests/test_packaging.py`：
  - `write_version` 寫出的檔案可以被 import，而且版本正確
  - `.desktop` 檔有 Exec、Icon、Type=Application
  - `build_appimage.sh` 在缺少參數時以非 0 退出（可以在 Linux 跑）
- [ ] **Step 2:** 實作。
- [ ] **Step 3:** 本機 Windows 打包仍然成功：`bash studio/build_exe.sh`。exe 開起來的版本是 `1.2.0-dev`，而且不會檢查更新（用 log 確認）。
- [ ] **Step 4:** 在 WSL 用 Linux 試打包：
  1. 建一個暫時的 venv，安裝 `requirements.txt` 和 `requirements-linux.txt`
  2. 執行 `pyinstaller` 產出 onedir
  3. 執行 `build_appimage.sh` 產出 AppImage
  4. 執行 `./xxx.AppImage --version`（`run_studio.py` 要支援 `--version`：印出版本號就結束）

  如果 WSL 缺少 FUSE，用 `--appimage-extract-and-run` 執行。結果寫進報告；做不到就記錄原因，交給 Task 6 的 CI 驗證。
- [ ] **Step 5: Commit**：`build: per-platform packaging (exe, macOS app, AppImage)`

---

### Task 6: GitHub Actions workflows

**Files:**
- Create: `.github/workflows/ci.yml`、`.github/workflows/release.yml`、`studio/ci/make_manifest.py`、`studio/tests/test_manifest.py`

**Interfaces:**
- `make_manifest.py <version> <assets_dir> <out_dir>`：
  - 輸入：`assets_dir` 裡有 4 個平台的成品，檔名依 Global Constraints。
  - 輸出：`out_dir/latest.json`（格式跟 Task 3 的 `parse_manifest` 一致）和 `out_dir/SHA256SUMS`（`sha256sum` 格式）。
  - 只用標準函式庫。少了任何一個平台的成品就 exit 1。
- **ci.yml**：
  - `on: [push, pull_request]`
  - job `test` 跑在 ubuntu-22.04，依序：
    1. checkout
    2. setup-python 3.12
    3. `pip install -r studio/requirements-ci.txt`（新增檔案，只含 `pillow`、`esptool`、`pyserial`，版本跟主 requirements 相同）
    4. 用 g++ 跑 host C++ 測試，指令跟 `menu.sh` 一致
    5. `python -m unittest discover -s studio/tests -t studio`
- **release.yml**：
  - **on**：
    - `push`：branches 是 `main` 和 `ci-test/**`，`paths` 是 Global Constraints 的允許清單
    - `workflow_dispatch`
  - `concurrency: release`
  - `permissions: contents: write`，只開在 release job，其他 job 用 read
  - **jobs**：
    1. `test`：跟 ci.yml 相同（用 reusable workflow `workflow_call`，或直接複製）。
    2. `version`：輸出 `version=<cat studio/VERSION>.${{ github.run_number }}`。
    3. `firmware`：
       - `container: espressif/idf:v5.5.<固定小版>`，查實際存在的 tag，要跟本機的 5.5.6 一致或最接近
       - build 一次，把 sha256 存起來；`idf.py fullclean` 後再 build 一次，比對 sha256，不同就讓 job 失敗
       - upload artifact `firmware`，內容是 bootloader、分割表、app 和 `flasher_args.json`，路徑結構跟 `build/` 一樣
    4. `package`：
       - matrix 是 `{windows-latest: windows-x64, macos-latest: macos-arm64, macos-15-intel: macos-x86_64, ubuntu-22.04: linux-x86_64}`
       - 每個平台都要：
         1. 下載 firmware artifact，放到 `studio/firmware/`
         2. 執行 `write_version.py`
         3. 安裝 `requirements.txt` 加上平台專用的 requirements
         4. 執行 `pyinstaller`
         5. 依平台把成品命名或打包
       - Linux 另外要：
         - `apt-get install` Qt 執行需要的函式庫（只裝打包需要的，並寫註解說明原因）
         - 下載 appimagetool（固定版本的網址，並驗證 SHA-256）
       - macOS 用 `ditto -c -k --keepParent` 打包成 zip
       - upload artifact
       - 另外把每個平台的 `pip freeze` 輸出存成 `requirements-lock-<平台代號>.txt` 並上傳為 artifact
         - 原因：macOS 和 Linux 的 lock 檔沒辦法在本機產生
         - Task 7 會把第一次 dry-run 產出的 lock 檔 commit 進 repo
         - 之後安裝時都加上 `-c` 指定 lock 檔，lock 檔存在時才加
    5. `release`：
       - `if: github.ref == 'refs/heads/main'`
       - 執行 `make_manifest.py`
       - `gh release create v$VERSION <assets> latest.json SHA256SUMS --title "PixelBoard Studio $VERSION" --notes "<自動產生：commit 訊息>"`
       - 用 `GH_TOKEN: ${{ secrets.GITHUB_TOKEN }}`
  - **actions 一律用 commit SHA 固定**，後面註解版本，例如 `actions/checkout@<sha> # v4.x.y`。SHA 要用 `curl https://api.github.com/repos/actions/checkout/git/ref/tags/<tag>` 查實際的值，不准猜。
  - **YAML 驗證**：用 Python 的 `yaml.safe_load` 確認可以解析。沒有 PyYAML 的話，用 `.venv-studio` 暫時安裝 `pyyaml`，但**不要把它加進專案的 requirements**。

- [ ] **Step 1:** 寫 `test_manifest.py`：
  - 4 個假成品 → 產出的 `latest.json` 可以被 `updater.parse_manifest` 解析，SHA256SUMS 的格式正確
  - 缺少一個平台時 exit 1
- [ ] **Step 2:** 實作 `make_manifest.py` 和兩個 workflow。
- [ ] **Step 3:** 確認 YAML 可以解析；全部測試 PASS。
- [ ] **Step 4: Commit**：`ci: test workflow and release pipeline`

---

### Task 7: README 和上線（需要使用者同意 push）

- [ ] **Step 1:** 更新 README.md：
  - 各平台的下載檔案
  - **第一次開啟時怎麼放行**：
    - Windows：SmartScreen 的「其他資訊」→「仍要執行」
    - macOS：「系統設定 → 隱私權與安全性 → 仍要打開」，或在 Finder 對程式按右鍵 →「打開」
    - Linux：`chmod +x`，並加入 dialout 群組
  - 自動更新怎麼運作
  - 給開發者的說明：發布流程、`studio/VERSION`、`ci-test/**` 分支的 dry-run
  - Commit：`docs: install, unblock and auto-update instructions`
- [ ] **Step 2: 停下來問使用者**：要 push `feature/studio` 到一個 `ci-test/release-dry-run` 分支，觸發 dry-run，驗證四個平台都打包成功。這需要 push，**一定要先取得同意**。
- [ ] **Step 3:** 同意後才 push，並觀察 workflow，用 `curl` 查 GitHub API 的 runs 和 jobs。失敗就修正後再 push 到同一個 `ci-test` 分支（這也在使用者同意的範圍內）。
  - 成功後，下載各平台的 `requirements-lock-*.txt` artifact，commit 進 `studio/`。
- [ ] **Step 4: 再停下來問使用者**：要合併到 `main` 並 push，產生第一個正式 Release。
- [ ] **Step 5:** 第一個 Release 出來之後，交給使用者實測清單：
  1. 從 Releases 下載 Windows 版並開啟（版本是 1.2.N）。
  2. 改一個會發布的小地方，例如更新對話框的文字，再發第二版。這次的 push 一樣要先問。
  3. 開啟 1.2.N，應該提示有新版。按更新後，應該重新開啟成 1.2.N+1。
  4. 有 macOS 或 Linux 機器的話一起測，沒有就記錄「未實測」。
