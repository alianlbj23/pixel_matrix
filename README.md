# pixel-board

ESP32-S3 + HUB75 LED 面板（每塊 64x32，最多串接 8 塊）的動畫與音樂播放板。板子開機後循環播放 GIF 動畫，並用內建音訊播放 MP3。內容透過 **PixelBoard Studio**（Windows、macOS、Linux 桌面程式）燒錄，不需要安裝 Python 或 ESP-IDF。

---

## 給使用者

### 1. 下載與開啟

到下載頁面：https://github.com/alianlbj23/pixel_matrix/releases/latest ，在最新版的 Assets 區挑出你的系統對應的檔案（檔名中的 `<版本>` 是版本號，例如 1.2.57）：

| 你的電腦 | 下載這個檔案 |
| --- | --- |
| Windows | `PixelBoard-Studio-<版本>-windows-x64.exe` |
| Mac（Apple 晶片：M1、M2、M3…） | `PixelBoard-Studio-<版本>-macos-arm64.zip` |
| Mac（Intel 處理器） | `PixelBoard-Studio-<版本>-macos-x86_64.zip` |
| Linux | `PixelBoard-Studio-<版本>-linux-x86_64.AppImage` |

不確定自己的 Mac 是哪一種：按螢幕左上角的蘋果選單 →「關於這台 Mac」，看「晶片」那一行。寫著 Apple M1／M2／M3… 就選 arm64；寫著 Intel 就選 x86_64。

這個程式沒有數位簽章，所以第一次開啟時系統會擋一下，照下面放行即可（只需要做一次）。

**Windows**

1. 把 exe 放在任何有寫入權限的資料夾，例如桌面，雙擊開啟。
2. 出現「Windows 已保護您的電腦」（SmartScreen）時，按「其他資訊」，再按「仍要執行」。
3. 如果出現缺少 Microsoft Edge WebView2 的訊息，請依訊息中的網址安裝後再開啟（Windows 11 通常已內建）。

**macOS**

1. 雙擊 zip 解壓縮，把解出來的 `PixelBoard Studio.app` 拖進「應用程式」資料夾。**第一次開啟前一定要先把它移出「下載項目」**：直接在「下載項目」裡開啟時，macOS 會把程式放在暫時的唯讀位置執行（App Translocation），自動更新就無法取代程式。
2. 第一次開啟會被擋下。到「系統設定 → 隱私權與安全性」，往下找到被擋下的訊息，按「仍要打開」（較舊的 macOS 寫「強制打開」），再確認一次。
3. macOS 14 以前也可以在 Finder 對程式按右鍵 →「打開」，再按一次「打開」；**macOS 15 以後已經沒有這個方法**，請用上面「系統設定 → 隱私權與安全性」的方式。

**Linux**

需要 glibc 2.34 以上的發行版（例如 Ubuntu 22.04、Debian 12 或更新的版本）。

1. 在檔案上開啟終端機，執行 `chmod +x PixelBoard-Studio-*.AppImage`（或在檔案屬性中勾選「允許執行檔案」）。
2. 要有權限使用 USB 序列埠：執行 `sudo usermod -aG dialout $USER`，然後**登出再登入**（或重新開機）。
3. 雙擊或在終端機執行 AppImage 開啟。要自動更新的話，**請直接執行下載的 AppImage 檔案**（不要用 `--appimage-extract` 解開後執行，也不要包進其他啟動器），程式才找得到要更新的檔案。

**接上板子**

用**可傳資料**的 USB-C 線，接到板子的 **Type-C 資料口**（不是標示 POWER 的口）。程式上方會顯示「找到板子」。找不到時按「重新尋找」。板子在各系統的序列埠名稱不同：Windows 是 COM 埠（例如 COM5）、Linux 是 `/dev/ttyACM0`、macOS 是 `/dev/cu.usbmodem…`，程式會自動尋找，不需要自己輸入。

> 如果你是用 WSL 的 usbipd 把板子接給 Linux，要先在 Windows PowerShell 執行 `usbipd detach --busid <BUSID>`，板子才會回到 Windows 使用。

### 自動更新

- 程式開啟時會在背景檢查有沒有新版（需要網路；檢查不到或失敗時不會打擾你）。有新版時會跳出視窗，說明新版本號和你的版本，問你是否更新。
- 按「更新」後，程式會下載新版、檢查檔案是否完整且沒被竄改（SHA-256），然後取代舊版並自動重新開啟。檢查不通過就不會安裝。
- 下載中可以按進度條旁的「取消」，或直接關閉視窗，舊版不受影響；開始安裝後就不能取消，請等它完成。
- **燒錄中不能更新**，更新提示會等燒錄結束才出現；更新進行中也不能開始燒錄。
- 更新會重新開啟程式，**目前加入的 GIF 和音樂需要重新加入**，面板排列會保留。
- 更新失敗時會顯示原因，你可以繼續使用目前的版本。
- 如果程式放在沒有寫入權限的資料夾，無法自己取代自己，這時會改為打開下載頁面，請手動下載新版。
- 從原始碼執行或開發中的版本不會自動更新。macOS 要把程式移出「下載項目」再開啟、Linux 要直接執行 AppImage 檔案，才能自動更新（見上面的說明）。
- macOS 更新時的記錄在 `~/Library/Logs/PixelBoard-Studio/update-helper.log`。

### 2. 第一次使用的步驟

1. **安裝/更新韌體**：按「安裝/更新韌體」。新板子或舊版韌體一定要做這一步。如果板子上已經是最新版，程式會告訴你不需要更新。
2. **設定面板數量**：在「面板排列」用 `-` / `+` 設定你接了幾塊面板（1 到 8 塊），或選「一排」「直疊」「方格」「蛇形」其中一種排法。
3. **辨識面板**：按「辨識面板」，每塊面板會顯示自己的編號和一個「上方」箭頭，顯示 5 秒後恢復原本的畫面。編號就是串接順序（第 1 塊是接在板子上的那塊）。
4. **排列**：在畫面上拖曳面板方塊，讓它跟你實際擺放的位置一致。方塊靠近時會自動吸附，不能重疊；點方塊可以旋轉（每次順時針 90 度），箭頭要跟實體面板上方一致。排列會自動儲存，下次開啟還在。
5. **加入 GIF 與音樂**：
   - 把 GIF 拖進視窗，或按「選擇 GIF」。多塊面板時可選「整張縮放」（整張圖鋪滿全部面板）或「每塊一份」（每塊面板各顯示一份，適合一對眼睛；面板要排成整齊的格子才能用，否則只能選「整張縮放」）。可以調整位置、大小、亮度。
   - 把 MP3 拖進視窗，或按「加入 MP3」。可以試聽、調整順序、刪除。下方的容量條顯示音樂用掉多少空間。沒有加入音樂時，板子上原本的音樂會保留。
6. **燒錄**：按「燒錄到板子」，確認畫面會告訴你將寫入什麼，按「開始燒錄」。燒錄中設定都會鎖住，請不要拔 USB 線。可以按「停止燒錄」中止；停止後板子上的 GIF 或音樂可能不完整，重新燒錄一次即可。完成後板子會自動重啟並開始播放。

### 3. 常見錯誤

| 訊息 | 意思與處理 |
| --- | --- |
| 找不到板子 | 請確認用的是資料線、接在 Type-C **資料口**；換一條線或換一個 USB 孔，再按「重新尋找」。 |
| 板子被其他程式占用中 | 序列監看器、Arduino IDE 等程式佔住了連接埠。關閉它們再試。 |
| 沒有權限使用 USB 序列埠 | Linux：執行 `sudo usermod -aG dialout $USER`，登出再登入後重試。 |
| 更新失敗：…，你可以繼續使用目前的版本 | 自動更新沒成功，舊版不受影響。稍後再試，或到下載頁面手動下載新版。 |
| 無法讓板子進入燒錄模式 | 請照下面的「BOOT 救援」操作。 |
| 燒錄中斷 | USB 線可能鬆脫。重新插上後再按一次燒錄。 |
| 這塊板子還沒有新版韌體，請先按「安裝/更新韌體」 | 辨識面板需要新版韌體，先安裝韌體。 |
| 辨識畫面還在顯示中 | 等 5 秒再按。 |
| 面板不能重疊 / 面板排得太開了 | 把面板放回不重疊的位置，整體範圍不要超過 1024 x 1024 個 LED。 |
| 板子沒有聲音 | 沒有加入音樂，或燒錄被停止。重新加入 MP3 並完整燒錄一次。 |

程式的記錄檔位置，回報問題時請附上：

| 系統 | 位置 |
| --- | --- |
| Windows | `%LOCALAPPDATA%\PixelBoard-Studio\studio.log` |
| macOS | `~/Library/Logs/PixelBoard-Studio/studio.log` |
| Linux | `~/.local/share/PixelBoard-Studio/studio.log`（有設定 `XDG_DATA_HOME` 時在該資料夾下） |

### 4. BOOT 救援

當程式無法讓板子進入燒錄模式，或板子完全沒有反應時：

1. 用 USB-C 資料線接上板子。
2. 按住 **BOOT** 鍵不放。
3. 按一下 **RESET** 鍵。
4. 放開 **BOOT**。
5. 回到程式再按一次「燒錄到板子」。

### 5. 電源提示

LED 面板很耗電。**3 塊以上**面板時，程式會顯示警告：請使用足夠大的 5V 電源（電流以面板規格為準，每塊面板最大可到數安培），不要只靠電腦 USB 供電，否則可能亮度不穩、畫面閃爍或板子重啟。亮度越高越耗電，USB 供電時建議不要超過 90。

---

## 給開發者

### 需求

- Linux 或 WSL2（Ubuntu），**ESP-IDF v5.5.x**。`menu.sh` 預設讀 `~/.espressif/tools/activate_idf_v5.5.6.sh`，路徑不同時用環境變數 `IDF_ACTIVATE` 指定。
- 本機建 Windows exe：Windows 端要有 python.org 的 Python 3.10（`%LOCALAPPDATA%\Programs\Python\Python310`），在 WSL 內執行腳本。macOS 與 Linux 的正式安裝檔由 CI 建置。
- 板子接在 WSL：先在 Windows PowerShell 執行 `usbipd attach --wsl --busid <BUSID>`。

### menu.sh

執行 `./menu.sh`，輸入數字選擇：

| 選項 | 功能 |
| --- | --- |
| 1 Build | `idf.py build` |
| 2 Flash | 選序列埠後 `idf.py flash`（含 bootloader、分割表、app、`assets/gif.pxg`、音樂） |
| 3 Serial log (monitor) | 序列監看器，Ctrl+] 離開 |
| 4 Build + flash + serial log | 1 + 2 + 3 |
| 5 Clean + rebuild | `fullclean` 後重新 build |
| 6 Host unit tests | 主機端 C++ 單元測試（font、tone、media、crc32、pxg、pxa、cmd、identify）和 Studio 的 Python 單元測試 |
| 7 Convert GIF | 選 `assets/gif/*.gif`，轉成 `assets/gif.pxg`；有 `assets/layout.json` 就依它的排列，否則用 `board_config.h` 的 `kPanelChain` |
| 8 Build Studio exe (Windows) | 先 build 韌體，再執行 `studio/build_exe.sh` |
| 9 Quit | 離開 |

Python 測試也可以直接跑：`.venv-studio/bin/python -m unittest discover -s studio/tests -t studio`（第一次跑選項 6 會建立 `.venv-studio`）。

### 建 exe（本機，Windows）

`menu.sh` 選 8，或在 build 完成後執行 `bash studio/build_exe.sh`。腳本會用 Windows 的 Python 3.10 建 venv（放在 `%LOCALAPPDATA%\pixelboard-studio-build`）、安裝 `studio/requirements.txt` 固定的套件（間接相依套件的版本由 `studio/requirements-lock.txt` 固定），再用 PyInstaller 打包，韌體映像（bootloader、分割表、app）會一起包進 exe。輸出 `dist/PixelBoard-Studio.exe`（`dist/` 不進版本控制），結尾會印出 Windows 路徑。如果舊的 exe 還在執行，最後複製會失敗，請先關閉它。本機建出來的版本號是 `<VERSION>.0-dev`，不會檢查更新。

### 建 Linux AppImage

CI 先用 PyInstaller 建出 onedir，再由 `studio/packaging/build_appimage.sh <onedir> <版本> <輸出.AppImage> <appimagetool>` 包成 AppImage。腳本不會下載任何東西，appimagetool 與 runtime 由呼叫端傳入（版本與 SHA-256 寫在腳本開頭註解，CI 設 `APPIMAGE_RUNTIME_FILE`）。macOS 的 `.app` 與 zip 由 CI 建置。

### 發布流程（CI）

- `.github/workflows/ci.yml`：每次 push 與 pull request 都跑測試（和 `menu.sh` 選項 6 相同的指令）。
- `.github/workflows/release.yml`：push 到 `main` 且變更的檔案在白名單內時，會先跑測試，再建韌體與四個平台的 Studio，最後自動發布 Release，版本號是 `v<studio/VERSION>.<workflow 執行編號>`，例如 `v1.2.57`。測試沒過就不會發布。白名單是 `release.yml` 開頭 `paths:` 列的路徑（`studio/pixelboard/`、`studio/packaging/`、`studio/ci/`、`studio/requirements*.txt`、`studio/VERSION`、`main/`、`partitions.csv`、`sdkconfig*`、`dependencies.lock`、`release.yml` 本身等）；只改 README、測試、`tools/` 等白名單以外的檔案不會發布。
- `studio/VERSION`：只放 `主版本.次版本`（目前 `1.2`），要升主/次版本時才改；第三碼由 CI 的執行編號自動產生。
- 韌體的 `PROJECT_VER`（目前 `1.2.0`）和 Studio 版本號無關，改韌體時要自己手動調整。韌體開了可重現建置（`CONFIG_APP_REPRODUCIBLE_BUILD=y`），CI 會建兩次，兩次 `pixel-board.bin` 的 SHA-256 不同就失敗。
- 演練（dry-run）：把分支 push 到 `ci-test/<任意名稱>`（例如 `ci-test/release-dry-run`），或在 Actions 頁面手動執行 `release` 並保持勾選 `dry_run`。會跑完所有測試與打包，但不會發布 Release。
- 鎖定檔：各平台各一份 `studio/requirements-lock-<key>.txt`（`<key>` 是 `windows-x64`、`macos-arm64`、`macos-x86_64`、`linux-x86_64`），由 CI 打包時產生並上傳成 artifact。第一次 dry-run 之後要把它們下載下來 commit 進 `studio/`，之後的打包才會用固定的間接相依版本。

### 開發者用的排列檔 assets/layout.json

選項 7 和 `tools/pack_gif.py --layout` 讀的排列檔格式和 Studio 儲存的一樣（Studio 自己的檔案在資料夾 `%LOCALAPPDATA%\PixelBoard-Studio\`（Windows）、`~/Library/Application Support/PixelBoard-Studio/`（macOS）或 `~/.local/share/PixelBoard-Studio/`（Linux）下的 `layout.json`，可以複製過來當範本）：

```json
{"version": 1, "panels": [
  {"index": 0, "x": 0, "y": 0, "rotation": 0},
  {"index": 1, "x": 64, "y": 0, "rotation": 0}
]}
```

`index` 是串接順序，`x`/`y` 是面板左上角在畫布上的 LED 座標，`rotation` 是順時針轉的角度（0/90/180/270）。面板不能重疊，最多 8 塊。

### 專案結構

| 路徑 | 內容 |
| --- | --- |
| `main/` | 韌體（HUB75 顯示、PXG 動畫、PXA 音樂、MP3 播放、USB 指令 `PXB IDENTIFY <n>`） |
| `partitions.csv` | 分割表：app 2 MB、gif 資料 3 MB（0x210000）、audio 資料 10 MB（0x510000） |
| `studio/pixelboard/` | Studio 的 Python 後端（排列、GIF 處理、PXG/PXA 打包、燒錄、辨識）和 `ui/`（HTML/JS） |
| `studio/tests/`、`test/host/` | Python 與 C++ 單元測試 |
| `studio/build_exe.sh`、`studio/PixelBoard-Studio.spec`、`studio/packaging/` | 各平台打包 |
| `.github/workflows/` | CI 測試與發布流程 |
| `tools/` | `pack_gif.py`、`pack_audio.py` 命令列工具 |
| `assets/` | 範例 GIF、音樂與 `gif.pxg` |
