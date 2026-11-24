# PixelBoard Studio：CI 自動發布與自動更新設計

日期：2026-11-24　狀態：草稿
相關文件：`2026-11-24-gif-studio-design.md`、`2026-11-24-multi-panel-design.md`（這兩份原本把 macOS、Linux 和自動更新列為非目標，本文件改為目標）

## 1. 這是什麼、為什麼要做

目前 PixelBoard Studio 的發布方式是：
1. 開發者在自己的 WSL 用 `studio/build_exe.sh` 打包。
2. 只有 Windows 版。
3. 更新要手動把 exe 傳給使用者。

使用者希望改成兩件事：
- **推上 `main` 就自動發布**，同時產出 Windows、macOS、Linux 三個平台的版本。
- **程式打開時偵測到新版本，會詢問是否更新**，按下去就自動完成。

**成功的標準：**
1. 開發者把修改推上 `main`，約 20–30 分鐘後，三個平台的新版本出現在 GitHub Releases。
2. 使用者打開舊版程式，看到「目前有新版本 X，是否更新？」，按「更新」後一分鐘內就換成新版並重新開啟，中間不需要任何手動步驟。

## 2. 使用者確認過的需求

以下是使用者原話或明確同意的內容：

1. **觸發條件**：每次推上 `main` 就自動發布。
2. **平台**：Windows、macOS、Linux 三個平台都要有。
3. **更新的體驗**：打開程式時，提示目前有新版本、詢問是否更新。
4. **燒錄時禁止更新**：燒錄中不能開始更新，提示也不能打斷燒錄。
5. **先不做程式簽章**：不付費購買 Apple 開發者帳號或 Windows 簽章憑證。README 要教使用者第一次開啟時如何放行。

## 3. 目標 / 非目標 / 反目標

### 目標
- **GitHub Actions**：推上 `main` 時依序執行：
  1. 跑全部測試（host C++ 和 Python）。
  2. 用 ESP-IDF build 韌體。
  3. 分別在三個平台打包 Studio。
  4. 建立 GitHub Release 並上傳成品。
  5. 測試或任何一步失敗，就不發布。
- **版本號自動產生**，每次發布都比上一次大。
- **程式啟動時檢查更新**：
  1. 有新版本時詢問。
  2. 按更新後，下載並驗證檔案（SHA-256）。
  3. 替換成新版，重新開啟。
  4. 任何一步失敗，都保留舊版繼續可用，並用白話告知。
- **燒錄中不更新**：檢查時發現新版本，提示延後到燒錄結束才出現；後端也要拒絕在燒錄中開始更新。
- **跨平台功能一致**：三個平台的 GIF、音樂、排列、辨識、燒錄功能都一樣。

### 非目標（這一版不做）
- 程式簽章、Apple 公證（使用者決定先不做）。
- 只下載差異的增量更新、測試版通道、降回舊版。
- 自動更新**板子上的韌體**。韌體只在使用者按燒錄時，依原本的 `plan_flash` 判斷要不要一起更新。
- ARM 版 Linux、32 位元系統。

### 反目標（出現就算失敗）
- 更新失敗後，使用者手上沒有能用的程式。
- 更新打斷燒錄，或是燒錄中可以開始更新。
- 沒通過測試的版本被發布出去。
- 版本號重複，或新版的版本號比舊版小，導致使用者一直被要求更新，或永遠收不到更新。
- 更新檔被竄改或下載不完整，卻照樣被安裝。
- 只改文件（README、docs）也觸發發布，讓使用者一直收到沒有實質內容的更新。

## 4. 限制

- **repo**：公開的 `alianlbj23/pixel_matrix`，GitHub Actions 對公開 repo 免費。目前的工作在分支 `feature/studio`，還沒合併到 `main` 也還沒 push。**合併和 push 要另外經使用者同意。**
- **使用者規則**：
  - CI/CD 的改動要先經使用者同意（本文件核准即同意建立 workflow）。
  - 新增相依套件要先問，並固定版本。
  - secret 只能從環境或 GitHub Secrets 讀取，這一版只用內建的 `GITHUB_TOKEN`。
  - 網路 I/O 都要設 timeout。
  - 外部輸入要驗證，包括 GitHub API 的回應和下載的檔案。
- **韌體**：需要 ESP-IDF v5.5.x（HUB75 函式庫不支援 6.x）。
- **USB 序列埠在各平台的名稱**：
  - Windows 是 `COMx`。
  - macOS 是 `/dev/cu.usbmodem*`。
  - Linux 是 `/dev/ttyACM*`，而且使用者要在 `dialout` 群組才有權限。
  - VID:PID `303a:1001` 的偵測方式三個平台都適用。
- **DTR=True 開 port 時不會讓板子重開**：只在 Linux 實測過，Windows 和 macOS 要在實測清單裡確認。

## 5. Claude 的技術決定（builder 可以挑戰）

### 5.1 CI 流程（`.github/workflows/release.yml`）
- **觸發**：`push` 到 `main`，改用 **`paths` 允許清單**，只有會影響成品的檔案有變動時才發布：
  - `studio/pixelboard/**`、`studio/run_studio.py`、`studio/PixelBoard-Studio.spec`、`studio/requirements*.txt`、`studio/VERSION`
  - `main/**`、`CMakeLists.txt`、`sdkconfig`、`sdkconfig.defaults`、`partitions.csv`、`dependencies.lock`
  - `.github/workflows/release.yml`
  - 只改測試、文件、`tools/`、`menu.sh` 或 `assets/` 時，不會發布新版。
  - 保留 `workflow_dispatch`，可以手動觸發，並提供「只打包、不發布」的 dry-run 選項。
  - 另外新增 `ci.yml`：每次 push 和每個 PR 都跑測試，但不發布。
- **Jobs**（前一個成功才跑下一個）：
  1. **test**（`ubuntu-latest`）：跑 host C++ 測試（g++）和 Python 測試，沿用 `menu.sh` do_tests 的指令。Python 套件依 lock 檔安裝。
  2. **firmware**：用 `espressif/idf:v5.5.x` 容器（固定小版本），執行 `idf.py build`，輸出 bootloader、分割表、app 和 `flasher_args.json` 作為 artifact。
  3. **package**，三個平台平行打包，都用 PyInstaller，並把 firmware artifact 一起包進去：
     - `windows-latest`：單一 `.exe`。
     - `macos-latest`（Apple Silicon）：`.app` 壓成 zip，用 ad-hoc 簽章，PyInstaller 預設就會做。
     - `ubuntu-22.04`：AppImage。選 22.04 是因為 glibc 較舊，能在比較多 Linux 版本上執行。
  4. **release**：
     - 建立 tag `v<版本>` 和 GitHub Release。
     - 上傳三個平台的成品，以及一份 `latest.json`。
     - `latest.json` 的內容：版本號，以及每個平台的檔名、大小和 SHA-256。
- **並行控制**：用 `concurrency: release`，同一時間只跑一個發布。
- **macOS 兩種架構都提供**：
  - Apple Silicon 在 `macos-latest` 上打包。
  - Intel 在 `macos-15-intel` 上打包。
  - `latest.json` 依架構分開記錄，程式更新時依 `platform.machine()` 下載對應的檔案。
  - 如果之後 GitHub 停掉 Intel runner，就移除這個 job，並在 README 註明。

### 5.2 版本號
- **大版本**寫在 `studio/VERSION`，內容例如 `1.2`，由開發者手動調整。
- **完整版本**是 `<大版本>.<GITHUB_RUN_NUMBER>`，例如 `1.2.57`。run number 在同一個 workflow 裡單調遞增，所以新版一定比舊版大。
- 打包時，把版本寫進程式內的 `_version.py`。本機開發時，版本顯示 `<大版本>.0-dev`，而且**不會檢查更新**。
- **韌體版本跟程式版本分開**，維持 `CMakeLists.txt` 的 `PROJECT_VER`，只在韌體真的有改動時手動調升。
  - 原因：如果韌體版本跟著每次發布改變，使用者每更新一次程式，下次燒錄都會被要求重寫韌體。
- **韌體要可重現建置**：在 `sdkconfig.defaults` 和 `sdkconfig` 開啟 `CONFIG_APP_REPRODUCIBLE_BUILD=y`。
  - 這樣同一份原始碼，在 CI 每次 build 出來的韌體位元組都相同，不會因為編譯時間或路徑不同而讓 MD5 對不上，被誤判成「韌體損壞」。
  - 開啟後韌體內容會改變，所以 `PROJECT_VER` 升到 `1.2.0`。
  - 已經燒過舊韌體的板子，在第一次 CI 發布後會**重寫一次韌體**，之後就不會再重寫。
  - CI 要驗證可重現性：firmware job build 兩次，比對 `pixel-board.bin` 的 SHA-256 一致，不一致就讓 CI 失敗。

### 5.3 檢查更新
- **時機**：程式啟動、畫面準備好之後，在背景執行緒檢查。
  1. 呼叫 `GET https://api.github.com/repos/alianlbj23/pixel_matrix/releases/latest`，timeout 5 秒。
  2. 從 release 的 assets 下載 `latest.json`，timeout 10 秒。
  3. 檢查失敗時（沒有網路、被限速、回應格式錯誤），只寫 log，不打擾使用者。
- **版本比較**：版本號必須是 `數字.數字.數字` 的格式，否則忽略。只有比目前版本大時才提示。
- **提示**：一個對話框，寫著「目前有新版本 X.Y.Z（你的版本 A.B.C），是否更新？」，按鈕是「更新」和「稍後」。
  - 如果目前已經加入 GIF 或音樂，對話框要提醒「更新會重新開啟程式，目前加入的 GIF 和音樂需要重新加入（面板排列會保留）」。
  - 這一版不做跨重啟的工作狀態保存。
  - 按「稍後」，這次不再提醒，下次開啟程式時再問。
  - 燒錄中先不顯示，等燒錄結束（完成、失敗或停止）才顯示。
- **鎖定**：`StudioApi` 新增 `check_update()`、`start_update()` 和 `update_progress` 回呼。燒錄中呼叫 `start_update()` 一律被拒絕；更新進行中，燒錄也會被拒絕，兩者互鎖。

### 5.4 下載與替換
1. 下載到暫存資料夾，並顯示進度。
   - 下載用 HTTPS，每塊資料的讀取都有 timeout。
   - 檔案大小必須符合 `latest.json` 的記錄，上限 600MB。Linux 的 Qt WebEngine 版本可能接近 300MB，所以上限放寬。
2. 比對 SHA-256，不符就刪掉檔案、停止更新，並告知使用者。
3. 依平台替換：
   - **Windows**：
     1. 執行中的 exe 不能被覆寫，但可以改名。所以先把它改名成 `PixelBoard-Studio.exe.old`。
     2. 把新檔放到原本的路徑。
     3. 啟動新版，舊版結束。
     4. 新版啟動時，刪掉留下的 `.old` 檔，刪不掉就記 log。
   - **macOS**：
     1. 新的 `.app` 解壓到暫存位置。
     2. 啟動一個獨立的小 shell 腳本：等舊程式的 PID 結束，再用新 `.app` 替換原本的 bundle（先改名備份，成功後才刪），然後 `open` 新版。
   - **Linux（AppImage）**：
     1. 從環境變數 `$APPIMAGE` 取得目前檔案的路徑。
     2. 把新檔寫到同一個資料夾的暫存檔，`chmod +x`，再用 `os.replace` 換上去。
     3. 啟動新版，舊版結束。
4. **任何一步失敗**：恢復原檔（如果已經改名），並顯示「更新失敗：<原因>，你可以繼續使用目前的版本」。
5. **沒有寫入權限時**（例如程式放在 `Program Files` 或 `/Applications`，又不是管理員）：改成打開 Release 下載頁面，並說明原因。

### 5.5 跨平台調整
- **介面框架**：`run_studio.py` 依平台選 pywebview 的 GUI：
  - Windows 用 `edgechromium`。
  - macOS 用 `cocoa`。
  - Linux 用 `qt`。Qt 可以整個包進 AppImage；GTK 版要依賴系統函式庫，不容易打包。
  - Qt 的 Python 綁定用 **PySide6**（LGPL 授權），不用 PyQt6（GPLv3）。用 PyQt6 會讓公開散布的 AppImage 受 GPL 約束。
- **WebView2 缺失的提示**只在 Windows 顯示。
- **系統對話框**：其他平台改用各自的方式，macOS 用 `osascript`，Linux 用 Qt 的 QMessageBox，或在終端機印出。
- **log 位置**：
  - Windows：`%LOCALAPPDATA%\PixelBoard-Studio\`
  - macOS：`~/Library/Logs/PixelBoard-Studio/`
  - Linux：`~/.local/share/PixelBoard-Studio/`
  - `layout.json` 放在同樣的位置。
- **Linux 序列埠沒有權限時**，把錯誤對應成白話：「沒有權限使用 USB 序列埠：請執行 `sudo usermod -aG dialout $USER` 後重新登入」。
- **打包腳本**：
  - `studio/PixelBoard-Studio.spec` 改成依平台產出不同格式（Windows onefile、macOS BUNDLE、Linux onedir，再由 appimagetool 包成 AppImage）。
  - CI 和本機的 `build_exe.sh` 共用同一份 spec。

### 5.6 需要新增的相依套件（固定版本，要經使用者同意）
- **Linux**：`PySide6`（含 Qt WebEngine，LGPL）、`qtpy`（pywebview 的 Qt 相依）。
- **macOS**：`pyobjc-core`、`pyobjc-framework-Cocoa`、`pyobjc-framework-WebKit`（pywebview 在 macOS 需要的套件，pip 會自動帶入，但要寫進 lock）。
- **CI 專用，不進程式**：
  - `appimagetool`：下載固定版本，並驗證 SHA-256。
  - GitHub 官方 actions：`actions/checkout`、`actions/setup-python`、`actions/upload-artifact`、`actions/download-artifact`。全部用 commit SHA 固定版本。
  - Release 用 `gh` CLI 建立（runner 內建），不用第三方 action。
- **每個平台各一份 lock 檔**：`requirements-lock-windows.txt`、`-macos.txt`、`-linux.txt`。

### 5.7 舊版本的保留
- 所有 Release 和 tag 都**保留，不自動刪除**。
  - 公開 repo 的 Release 檔案不額外收費。
  - 刪除 tag 和 Release 依使用者規則需要另外同意。
- 如果之後要清理，再另外討論。

## 6. 留給 builder 決定
- 更新對話框和下載進度的視覺設計。
- `latest.json` 的確切欄位名稱。
- AppImage 的 desktop 檔和圖示。
- 測試的切法。以下這些應該可以在不連網、不需要真的 GitHub 的情況下做單元測試：
  - 版本比較
  - `latest.json` 驗證
  - SHA-256 驗證
  - 各平台替換流程（用暫存資料夾模擬）
  - 互鎖
- CI 在 PR 上只跑測試、不發布，可以做，但不是必要。

## 7. 實測（交付前）
1. 在一個測試用的 tag 或分支上，完整跑一次 workflow，確認三個平台都產出成品。可以先用 `workflow_dispatch` 加一個不建立 release 的 dry-run 選項。
2. Windows：用舊版 exe 測試更新到新版。
3. macOS、Linux：使用者有機器的話一起測，沒有的話記錄「未實測」。
