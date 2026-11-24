# PixelBoard Studio Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 做出單一 `PixelBoard-Studio.exe`：使用者拖進 GIF 和 MP3，依 1 或 2 塊面板忠實預覽、調整，然後一鍵燒進 ESP32-S3 板子。

**Architecture:** 韌體改成開機時從 flash 分割區讀取 GIF 和音樂：GIF 用 PXG1 格式放在 `gif` 分割區，音樂用 PXA1 格式放在 `audio` 分割區，不再編譯進程式，也不再用 SPIFFS。Windows 程式用 Python 撰寫，`studio/pixelboard/` 是純邏輯套件，可以在 WSL 用 unittest 測試，負責 GIF 處理、格式打包、判斷燒錄計畫。介面用 pywebview（WebView2）顯示 HTML/JS，燒錄透過 esptool 的 Python API，最後用 PyInstaller 打包成單一 exe。

**Tech Stack:** ESP-IDF v5.5（C++），Python 3.10（Windows 打包）/ WSL 系統 Python 3（測試），Pillow，pywebview 6.2.1，esptool 5.6.0，pyserial 3.5，PyInstaller 6.22.3，unittest，host 端 g++ 測試。

**Spec:** `docs/superpowers/specs/2026-11-24-gif-studio-design.md`

## Global Constraints

- **相依套件**：固定版本，寫在 `studio/requirements.txt`：
  - `pywebview==6.2.1`
  - `esptool==5.6.0`
  - `pyserial==3.5`
  - `pillow==12.3.0`
  - `pyinstaller==6.22.3`

  除此之外不准再加新套件。如果真的需要，先停下來問使用者。
- **純邏輯套件的相容性**：`studio/pixelboard/` 也要能在 WSL 系統的 Pillow 10.2.0 上跑測試。不能用 Pillow 11 以後才有的 API。
- **分割表**：

  | 名稱 | 類型 | 位置 | 大小 |
  |---|---|---|---|
  | nvs | data, nvs | 0x9000 | 0x6000 |
  | phy_init | data, phy | 0xf000 | 0x1000 |
  | factory | app, factory | 0x10000 | 2M |
  | gif | data, 0x40 | 0x210000 | 1M |
  | audio | data, 0x41 | 0x310000 | 12M |

  gif 和 audio 的子類型用自訂值 0x40 和 0x41，不再是 spiffs。
- **GIF 的限制**：
  - 最多 64 格
  - 每格 20–10000 ms，沒有設定就用 100 ms
  - 輸入檔最大 10MB
  - 面板固定 64x32，串接數只能是 1 或 2
- **亮度**：範圍 10–128，預設 90。
- **MP3 的限制**：
  - 取樣率只能是 8000、11025、12000、16000、22050、24000、32000、44100 或 48000 Hz
  - 單聲道或雙聲道
  - 檔名最長 60 bytes（UTF-8）
  - 全部檔案加上標頭不能超過 12MB（12582912 bytes）
- **USB 裝置**：VID:PID 是 `303a:1001`。
- **韌體識別**：`project_name` 是 `pixel-board`，版本由根目錄 `CMakeLists.txt` 的 `PROJECT_VER` 決定，第一版是 `"1.0.0"`。
- **使用者看到的文字**：一律用繁體中文白話。程式碼、log 和識別名稱用英文。
- **錯誤處理**：
  - 不准吞例外，錯誤要記 log 並回報到介面。
  - 序列埠和燒錄都要設 timeout。
  - 外部輸入（檔案、序列埠資料）都要先驗證。
- **Git**：
  - 在分支 `feature/studio` 上工作。
  - 每個 task 完成後 commit。
  - **不准 push**，要先問使用者。
- **完成的定義**：
  - `menu.sh` 選項 6 的 host 測試全部通過
  - `idf.py build` 沒有新的 warning
  - `python3 -m unittest discover -s studio/tests` 通過

## Review Focus

這五個情況最容易出問題，測試要涵蓋到：

1. **燒錄到一半拔掉 USB，或寫入失敗**：gif 區或 audio 區會留下殘缺資料。韌體的 CRC 檢查要能擋下來，畫面顯示 NO GIF 或靜音，不能當機或全黑。介面要顯示白話錯誤，並提示重插再按一次燒錄。
   - Task 3：測試截斷的 PXG1 會被拒絕
   - Task 5：測試截斷的 PXA1 會被拒絕
   - Task 6：測試錯誤訊息的對照
2. **板子被別的程式占用，或看不到**：
   - COM port 被序列監看器占用
   - 板子 attach 在 WSL，Windows 看不到
   - 同時插了兩塊板子
   - USB 線只能充電，沒有資料

   每種都要有對應的中文提示（Task 1 和 Task 6 的測試）。
3. **板子上原本的韌體不是新版**：
   - Waveshare 出廠 demo
   - 舊版 pixel-board，用的是 3MB factory 加 SPIFFS 的舊分割表

   計畫要選對寫入範圍。分割表不同時，一定要寫入新的分割表；舊的 SPIFFS 音樂讀不出來時，不能讓韌體當機（Task 5 和 Task 6）。
4. **GIF 輸入的極端情況**：
   - 副檔名改成 .gif 的 PNG
   - 只有 1 格的靜態 GIF
   - 完全透明
   - 超過 64 格
   - 每格時間是 0
   - 超大尺寸，例如 4000x4000

   每種都要有明確的錯誤訊息，或正確處理（Task 2）。
5. **MP3 輸入的極端情況**：
   - 有 ID3v2 標籤
   - VBR
   - 96kHz
   - 檔名中文超過 60 bytes
   - 不是 MP3 卻用 .mp3 副檔名
   - 總容量超過 12MB

   加入時就要擋下，並給中文原因（Task 4）。

---

## File Structure

```
partitions.csv                      修改：新分割表
CMakeLists.txt                      修改：PROJECT_VER "1.0.0"
main/board_config.h                 修改：kPanelChain 只當作預設值
main/pxg.h / pxg.cpp                新增：PXG1 解析（純 C++，可在 host 測試）
main/pxa.h / pxa.cpp                新增：PXA1 解析（純 C++，可在 host 測試）
main/crc32.h / crc32.cpp            新增：CRC-32/ISO-HDLC（純 C++），跟 Python zlib.crc32 相同
main/font5x7.cpp                    修改：加入 N G I F 字元
main/main.cpp                       修改：從 gif 分割區讀取並初始化面板，失敗時顯示 NO GIF
main/mp3_player.cpp                 修改：從 audio 分割區（PXA1）串流讀取
main/gif_anim.h, gif_anim_data.cpp  刪除
main/CMakeLists.txt                 修改：移除 spiffs，加入 flash-to-partition
tools/gif2c.py                      刪除（功能移到 studio/pixelboard/gifproc.py）
tools/pack_gif.py                   新增：CLI，assets/gif → assets/gif.pxg（開發者用）
tools/pack_audio.py                 新增：CLI，assets/audio → build/audio.pxa（只用標準函式庫，給 CMake 呼叫）
studio/pixelboard/__init__.py
studio/pixelboard/gifproc.py        GIF 解碼、版面、調整，輸出 RGB888 frames
studio/pixelboard/pxg.py            PXG1 打包和解析
studio/pixelboard/pxa.py            PXA1 打包和解析
studio/pixelboard/mp3info.py        MP3 格式檢查（只用標準函式庫）
studio/pixelboard/board.py          找 COM port、讀板子狀態、決定燒錄計畫、錯誤訊息對照
studio/pixelboard/flasher.py        esptool API 包裝，支援進度回報
studio/pixelboard/firmware.py       定位 exe 內附的韌體檔（支援 PyInstaller）
studio/pixelboard/app.py            pywebview 視窗和 StudioApi（JS 呼叫的入口）
studio/pixelboard/ui/index.html, app.js, style.css
studio/run_studio.py                exe 的進入點
studio/requirements.txt
studio/PixelBoard-Studio.spec       PyInstaller 設定
studio/build_exe.sh                 從 WSL 呼叫 Windows 的 Python 打包
studio/tests/test_*.py              unittest
test/host/test_pxg.cpp, test_pxa.cpp, test_crc32.cpp
menu.sh                             修改：Convert GIF 改用 pack_gif，新增 Build Studio exe，測試加入新項目
```

`tools/` 和 `studio/` 共用 `studio/pixelboard/` 這個套件。CLI 工具用 `sys.path.insert` 載入它，不要複製程式碼。

---

### Task 0: 開分支

- [ ] **Step 1:** `git switch -c feature/studio`。確認 `git status` 是乾淨的。如果有未 commit 的改動（上一輪的雙面板眼睛和 .gitignore），先 commit：`git commit -m "feat: two chained panels with split eyes GIF"`。

---

### Task 1: 最小 exe：自動找到板子並燒錄目前的韌體（第一個里程碑）

這一步先處理最大的風險：在 Windows 上打包、WebView2 能顯示、esptool API 能燒錄 S3 的 USB-JTAG。這時還用**現有的分割表和韌體**。

**Files:**
- Create: `studio/pixelboard/__init__.py`、`board.py`、`flasher.py`、`firmware.py`、`app.py`、`ui/index.html`、`ui/app.js`、`ui/style.css`、`studio/run_studio.py`、`studio/requirements.txt`、`studio/PixelBoard-Studio.spec`、`studio/build_exe.sh`
- Test: `studio/tests/test_board_ports.py`
- Modify: `menu.sh`（新增選項 "Build Studio exe"）、`.gitignore`（加入 `dist/`、`studio/build/`）

**Interfaces:**
- Produces:
  - `board.PortInfo = NamedTuple(device: str, vid: int|None, pid: int|None, description: str)`
  - `board.find_board_ports(ports: list[PortInfo]) -> list[str]`：回傳 VID:PID 是 `303a:1001` 的 `device`，依名稱排序
  - `board.list_system_ports() -> list[PortInfo]`：包裝 `serial.tools.list_ports.comports()`
  - `board.BoardError(Exception)`：有 `.user_message: str` 屬性，存中文訊息
  - `flasher.Flasher(port: str, progress: Callable[[str, float], None])`，提供：
    - `.connect()`
    - `.write(regions: list[tuple[int, bytes]])`
    - `.read(offset: int, size: int) -> bytes`
    - `.hard_reset()`
    - `.close()`
    - 失敗時丟出 `BoardError`
  - `firmware.bundled_dir() -> pathlib.Path`：打包後是 `sys._MEIPASS/firmware`，開發時是 repo 的 `build/`
  - `firmware.load_images() -> dict[str, bytes]`：key 是 `bootloader`、`partition_table`、`app`
  - `app.StudioApi`：這一步只有兩個方法：
    - `find_board() -> dict`：回傳 `{"ports": [...], "message": str}`
    - `flash_firmware() -> dict`：回傳 `{"ok": bool, "message": str}`
  - JS 端的進度回呼：`window.studio.onProgress(stage, fraction)`

- [ ] **Step 1: Write the failing test** `studio/tests/test_board_ports.py`

```python
class FindBoardPortsTest(unittest.TestCase):
    def test_picks_only_esp32s3_usb_jtag(self):
        ports = [PortInfo("COM3", 0x10C4, 0xEA60, "CP2102"),
                 PortInfo("COM11", 0x303A, 0x1001, "USB JTAG/serial debug unit"),
                 PortInfo("COM1", None, None, "Communications Port")]
        self.assertEqual(find_board_ports(ports), ["COM11"])
    def test_two_boards_sorted(self):
        ports = [PortInfo("COM12", 0x303A, 0x1001, ""), PortInfo("COM11", 0x303A, 0x1001, "")]
        self.assertEqual(find_board_ports(ports), ["COM11", "COM12"])
    def test_none_found(self):
        self.assertEqual(find_board_ports([]), [])
```

- [ ] **Step 2:** 執行 `python3 -m unittest discover -s studio/tests -v`。預期 FAIL：`ModuleNotFoundError: pixelboard`。在 `studio/tests/__init__.py` 用 `sys.path` 加入 `studio/`。

- [ ] **Step 3: 實作 `board.find_board_ports`、`board.list_system_ports`、`BoardError`。**

- [ ] **Step 4:** 跑同一個測試指令，預期 PASS。

- [ ] **Step 4b: 錯誤對照測試**：在 `studio/tests/test_board_ports.py` 新增 `test_error_mapping`，測試純函式 `flasher.to_board_error(exc: Exception) -> BoardError`：
  - `serial.SerialException("could not open port 'COM11': PermissionError(13, ...)")` → user_message 含「被其他程式占用」
  - esptool 的 `FatalError("Failed to connect ...")` → 含「BOOT」
  - `serial.SerialException("write failed")` → 含「燒錄中斷」
  - 其他例外 → 含「未預期的錯誤」，並附上原始訊息

- [ ] **Step 5: 實作 `flasher.Flasher` 和 `flasher.to_board_error`**
  - 使用 esptool 5.6.0 的公開 API：`esptool.cmds` 裡的 `detect_chip`、`run_stub`、`attach_flash`、`write_flash`、`read_flash`、`reset_chip`。實作前先用 context7 查 esptool 5.x 的 scripting 文件確認簽名。
  - 進度：用 esptool 5 的 logger 自訂介面接到 `progress` 回呼。
  - 連線：`connect_attempts=3`，baud 460800。
  - 例外對照：esptool 和 pyserial 的例外要轉成 `BoardError`，訊息如下：

    | 情況 | 訊息 |
    |---|---|
    | port 開不起來，被占用 | 「板子被其他程式占用中（例如序列監看器），請關閉後再試」 |
    | 連線逾時 | 「無法讓板子進入燒錄模式：請按住 BOOT 鍵、按一下 RESET、放開 BOOT 後再試」 |
    | 寫入中斷 | 「燒錄中斷，請確認 USB 線沒有鬆脫，重新插上後再按一次燒錄」 |

- [ ] **Step 6: 實作 `firmware.py`、`app.py`、`run_studio.py` 和最小 UI。** UI 只有三個元素：「尋找板子」按鈕、狀態文字、「燒錄韌體」按鈕和進度條。`flash_firmware` 依照 `build/flasher_args.json` 的 offset 寫入 bootloader（0x0）、分割表（0x8000）和 app（0x10000）。
  - 沒找到板子時，顯示：「找不到板子。請用可傳資料的 USB 線接到板子的 Type-C 資料口（不是 POWER 口）」
  - 找到多塊時，用下拉選單讓使用者選。
  - 如果 `run_studio.py` 啟動 pywebview 時發現缺少 WebView2，就攔下這個例外，用 `ctypes.windll.user32.MessageBoxW` 跳出中文說明：「這台電腦缺少 Microsoft Edge WebView2，請到 https://go.microsoft.com/fwlink/p/?LinkId=2124703 安裝後再開啟」，同時把錯誤寫進 log。
  - log 檔放在 `%LOCALAPPDATA%\PixelBoard-Studio\studio.log`。

- [ ] **Step 7: 寫打包流程**
  - `studio/requirements.txt` 寫入 Global Constraints 裡的 5 個固定版本。
  - `studio/build_exe.sh` 依序做這些事：
    1. 把 `studio/` 和 `build/` 裡的 `bootloader/bootloader.bin`、`partition_table/partition-table.bin`、`pixel-board.bin`、`flasher_args.json` 複製到 Windows 的暫存資料夾 `%LOCALAPPDATA%\pixelboard-studio-build`。
    2. 如果沒有 venv，就用 `C:\Users\<user>\AppData\Local\Programs\Python\Python310\python.exe -m venv` 建立，再 `pip install -r requirements.txt`。
    3. 執行 `pyinstaller --noconfirm PixelBoard-Studio.spec`。
    4. 把 `dist/PixelBoard-Studio.exe` 複製回 repo 的 `dist/`。
  - Windows 使用者名稱從 `cmd.exe /c echo %USERNAME%` 取得，不能寫死。
  - `.spec` 的設定：`onefile`、`windowed`，`datas` 包含 `pixelboard/ui` 和 `firmware/`。
  - `menu.sh` 新增選項 "Build Studio exe"，依序執行 `do_build` 和 `studio/build_exe.sh`。

- [ ] **Step 8: 實機驗證**
  1. 在 PowerShell 執行 `usbipd detach --busid <BUSID>`，讓 Windows 看得到板子。
  2. 點兩下 `dist/PixelBoard-Studio.exe`。
  3. 按「尋找板子」，預期顯示 COM11（或實際的 port）。
  4. 按「燒錄韌體」，預期進度條走完，板子重開後畫面跟現在一樣（雙面板眼睛和音樂）。
  5. 用序列監看器占著 port 再試一次，預期出現「被其他程式占用」的訊息。

  把結果寫進 commit 訊息。如果 WebView2、PyInstaller 或 esptool 任何一個不能用，**停下來回報使用者**，不要換技術。

- [ ] **Step 9: Commit**：`git commit -m "feat(studio): minimal exe that finds the board and flashes firmware"`

---

### Task 2: GIF 處理核心（gifproc），取代 gif2c

**Files:**
- Create: `studio/pixelboard/gifproc.py`
- Test: `studio/tests/test_gifproc.py`（把 `test/host/test_gif2c.py` 的 16 個測試全部搬過來並改名，接著加新的測試）
- Delete: `tools/gif2c.py`、`test/host/test_gif2c.py`

**Interfaces:**
- Produces:
  - `gifproc.GifError(Exception)`：有 `.user_message` 屬性
  - `gifproc.Layout = dataclass(chain: int = 1, split: bool = False, offset_x: int = 0, offset_y: int = 0, scale: float = 1.0)`
    - **沒有 swap 欄位**：左右對調是補償實體排線順序用的，只寫進 PXG1 旗標，由韌體在畫圖時處理。預覽一律顯示使用者想要的樣子。
    - `chain` 只能是 1 或 2
    - `scale` 範圍 0.25–4.0，是在「自動適配大小」之上再乘的倍數
    - `offset_x` 和 `offset_y` 以 LED 像素為單位，範圍 -128..128
  - `gifproc.SourceGif`：由 `gifproc.load(path) -> SourceGif` 產生，存放已合成的 RGBA frames 和 durations，**只解碼一次**
  - `gifproc.render(src: SourceGif, layout: Layout) -> list[tuple[bytes, int]]`：每格 `(64*chain)*32*3` bytes
  - 常數沿用 gif2c：
    - `MAX_FRAMES=64`、`MAX_INPUT_BYTES=10*1024*1024`
    - `DEFAULT_DURATION_MS=100`、`MIN_DURATION_MS=20`、`MAX_DURATION_MS=10000`
    - `PANEL_W=64`、`PANEL_H=32`

**版面規則**，演算法沿用 gif2c：
- `split=False`：在整個 `64*chain x 32` 畫布上等比縮放、置中。
- `split=True` 且 `chain=2`：取所有格可見內容的聯集範圍，切成 2 份，每份用相同比例放進自己的 64x32。
- 縮放和平移：「自動適配比例 x scale」之後，再平移 `(offset_x, offset_y)`。
- 分割模式下，每塊面板各自平移並裁切，不會畫到隔壁面板。

- [ ] **Step 1: 搬移測試**：改成 `load` 加 `render` 的呼叫方式，原本的期望值不變。

- [ ] **Step 2: 寫新的失敗測試**

```python
def test_offset_moves_content_by_pixels(self):         # offset_x=3：每個非黑像素往右移 3 格
def test_scale_half_shrinks_around_center(self):       # scale=0.5：內容寬度約為原本一半，仍置中
def test_split_offset_clipped_per_panel(self):         # split，offset_x=60：左眼內容不會出現在 x>=64
def test_layout_validation(self):                       # chain=3、scale=0、offset=999、split 但 chain=1 → GifError
def test_png_renamed_gif_rejected(self):                # user_message 含「不是 GIF」
def test_huge_dimensions_ok(self):                      # 4000x4000 單格 GIF 可以正常處理，輸出尺寸正確
def test_zero_duration_uses_default(self):              # duration=0 → 100
```

- [ ] **Step 3:** 執行 `python3 -m unittest studio.tests.test_gifproc -v`，預期新測試 FAIL。
- [ ] **Step 4: 實作 `gifproc.py`。**
- [ ] **Step 5:** 再跑一次，預期全部 PASS。同時更新 `menu.sh` 的 `do_tests`，把 gif2c 換成 `python3 -m unittest discover -s studio/tests`。
- [ ] **Step 6: Commit**：`feat(studio): gifproc with offset/scale/swap layout`

---

### Task 3: PXG1 格式：Python 打包和韌體讀取

**PXG1 位元組格式**（little-endian，標頭 32 bytes）：

| 位移 | 型別 | 欄位 |
|---|---|---|
| 0 | char[4] | magic `"PXG1"` |
| 4 | u16 | 格式版本，等於 1 |
| 6 | u16 | panel_w，等於 64 |
| 8 | u16 | panel_h，等於 32 |
| 10 | u8 | chain，1 或 2 |
| 11 | u8 | brightness，10..128 |
| 12 | u8 | flags，bit0 = swap |
| 13 | u8[3] | 保留，填 0 |
| 16 | u16 | frame_count，1..64 |
| 18 | u16 | 保留，填 0 |
| 20 | u32 | payload_len |
| 24 | u32 | crc32 |
| 28 | u32 | 保留，填 0 |

- `crc32` 用 zlib.crc32 計算，範圍是整個 payload。
- 標頭之後依序放 payload：
  1. `frame_count` 個 u16 的 duration_ms
  2. 補 0 對齊到 4 bytes
  3. 各格的 RGB888 資料，每格 `64*chain*32*3` bytes

**Files:**
- Create: `studio/pixelboard/pxg.py`、`tools/pack_gif.py`、`main/pxg.h`、`main/pxg.cpp`、`main/crc32.h`、`main/crc32.cpp`
- Test: `studio/tests/test_pxg.py`、`test/host/test_pxg.cpp`、`test/host/test_crc32.cpp`、`test/host/fixtures/sample.pxg`（由 Python 產生並 commit，用來驗證兩邊格式一致）
- Modify: `partitions.csv`、`CMakeLists.txt`（`set(PROJECT_VER "1.0.0")`）、`main/CMakeLists.txt`、`main/main.cpp`、`main/font5x7.cpp`、`main/board_config.h`、`menu.sh`（Convert GIF 改成呼叫 `tools/pack_gif.py`，輸出 `assets/gif.pxg`）
- Delete: `main/gif_anim.h`、`main/gif_anim_data.cpp`

**Interfaces:**
- Produces（Python）：
  - `pxg.pack(frames: list[tuple[bytes,int]], chain: int, brightness: int, swap: bool) -> bytes`
  - `pxg.unpack(data: bytes) -> pxg.Animation`：`Animation` 有 `chain`、`brightness`、`swap`、`frames` 欄位
  - 錯誤時丟出 `pxg.PxgError`
  - `pxg.MAX_BYTES = 0x100000`
- Produces（C++）：
  - `uint32_t crc32::update(uint32_t crc, const uint8_t* data, size_t len)`：初始值 0，結果跟 zlib 相同
  - `bool pxg::parse(const uint8_t* data, size_t len, pxg::View* out, const char** err)`
  - `pxg::View` 的欄位：`chain`、`brightness`、`swap`、`frame_count`、`const uint16_t* durations`（可能沒對齊，讀取時用 memcpy）、`const uint8_t* frames`、`size_t frame_bytes`
  - `int pxg::panel_x(int x, uint8_t chain, bool swap)`：把資料的 x 對應到面板實際的 x。`swap` 為 true 而且 chain 是 2 時，`x<64` 對應到 `x+64`，`x>=64` 對應到 `x-64`；其他情況原樣回傳。
- `main.cpp` 的流程：
  1. 用 `esp_partition_find_first(ESP_PARTITION_TYPE_DATA, 0x40, "gif")` 找分割區，再用 `esp_partition_mmap` 對應到記憶體。
  2. 呼叫 `pxg::parse`。
  3. 成功：用 `View` 的 chain 和 brightness 初始化面板；`swap` 在畫圖時處理。
  4. 失敗：用 `board_config.h` 的預設值，畫面置中顯示白色「NO GIF」，並記錄錯誤原因。

- [ ] **Step 1: 寫失敗測試**
  - Python：
    - `test_roundtrip`
    - `test_bad_magic`
    - `test_crc_mismatch`
    - `test_truncated`：截斷到 len-1 要丟 `PxgError`
    - `test_too_big`：65 格要丟 `PxgError`
    - `test_brightness_range`：9 和 129 都要被拒絕
  - C++：
    - `test_parse_fixture`：解析 `sample.pxg`，chain=2、brightness=90、frame_count=3，durations 是 {100, 150, 200}，第 0 格第一個像素是 (255, 0, 0)
    - 拒絕 bad magic
    - 拒絕版本 2
    - 拒絕 CRC 錯誤
    - 拒絕 payload 被截斷
    - 拒絕 chain=3
  - CRC：`crc32::update(0, "123456789", 9) == 0xCBF43926`
  - `panel_x`：
    - `panel_x(0, 2, true) == 64`
    - `panel_x(127, 2, true) == 63`
    - `panel_x(5, 2, false) == 5`
    - `panel_x(5, 1, true) == 5`
  - 字型：`font5x7::glyph('N'/'G'/'I'/'F') != nullptr`
- [ ] **Step 2:** 執行測試，預期 FAIL。
- [ ] **Step 3: 實作**
  - Python 和 C++ 的 PXG1 讀寫，以及 CRC。
  - 字型加入 N、G、I、F。
  - 分割表、根目錄 CMake、main.cpp。
  - `main/CMakeLists.txt`：
    - 移除 `gif_anim_data.cpp`。
    - 如果 `assets/gif.pxg` 存在，就加上 `esptool_py_flash_to_partition(flash "gif" ${PROJECT_DIR}/assets/gif.pxg)`。
  - 用 `tools/pack_gif.py` 產生 `assets/gif.pxg`（eyes.gif，split，chain=2），並 commit。
- [ ] **Step 4:** 測試全部 PASS，`idf.py build` 成功。
- [ ] **Step 5: 實機驗證**
  - 燒錄後雙面板眼睛跟之前一樣。
  - 執行 `esptool erase_region 0x210000 0x1000`，預期畫面顯示 NO GIF，log 有 `pxg: bad magic`。
- [ ] **Step 6: Commit**：`feat: PXG1 gif partition read at boot`

---

### Task 4: PXA1 格式和 MP3 檢查（Python）

**PXA1 位元組格式**（little-endian）：

標頭 32 bytes：

| 位移 | 型別 | 欄位 |
|---|---|---|
| 0 | char[4] | magic `"PXA1"` |
| 4 | u16 | 格式版本，等於 1 |
| 6 | u16 | file_count，0..64 |
| 8 | u32 | payload_len |
| 12 | u32 | crc32 |
| 16 | u8[16] | 保留，填 0 |

- `crc32` 的範圍是整個 payload，也就是檔案表加資料。
- 標頭後面接檔案表，每筆 72 bytes：

  | 位移 | 型別 | 欄位 |
  |---|---|---|
  | 0 | u8[64] | 檔名，UTF-8，用 0 補滿 |
  | 64 | u32 | 從分割區起點算的位移 |
  | 68 | u32 | 長度 |

- 檔案表之後依序放 MP3 原始資料，每個檔案的起點對齊到 4 bytes。

**Files:**
- Create: `studio/pixelboard/pxa.py`、`studio/pixelboard/mp3info.py`、`tools/pack_audio.py`
- Test: `studio/tests/test_pxa.py`、`studio/tests/test_mp3info.py`、`studio/tests/fixtures/`

  測試用的 fixtures 在測試時用程式產生，產生方式跟之前驗證過的一樣：由 MPEG-1 Layer III 靜音 frame 組成的合法 MP3（標頭 `FF FB 90 64`，每個 frame 417 bytes）。另外再做一份加上 ID3v2 的版本，以及一份 96kHz 的標頭。

**Interfaces:**
- Produces：
  - `mp3info.probe(data: bytes) -> mp3info.Info(sample_rate: int, channels: int)`
    - 會跳過 ID3v2 標籤，並要求連續找到 3 個同步的 frame header
    - 失敗時丟出 `mp3info.Mp3Error`，有 `.user_message` 屬性
  - `pxa.Track = NamedTuple(name: str, data: bytes)`
  - `pxa.pack(tracks: list[Track]) -> bytes`
  - `pxa.unpack(data: bytes) -> list[Track]`
  - 錯誤時丟出 `pxa.PxaError`
  - `pxa.CAPACITY = 12 * 1024 * 1024`
  - `pxa.validate_track(name: str, data: bytes) -> None`：組合檔名長度和 `mp3info` 的檢查，並且只接受 Global Constraints 列出的取樣率
- `tools/pack_audio.py`：
  - 用法：`pack_audio.py <dir> <out>`
  - 只收 `*.mp3`，依檔名排序
  - 只用標準函式庫，因為 IDF 的 Python 環境沒有 Pillow

- [ ] **Step 1: 寫失敗測試**
  - `test_probe_plain`：44100 Hz、2 聲道
  - `test_probe_id3v2`
  - `test_probe_mono`
  - `test_reject_96k`：user_message 含「取樣率」
  - `test_reject_not_mp3`
  - `test_pack_roundtrip_order`：播放順序跟輸入順序相同
  - `test_name_61_bytes_rejected`
  - `test_capacity_exceeded`：user_message 含「容量」
  - `test_crc_mismatch`
  - `test_empty_list_valid`：file_count=0
- [ ] **Step 2:** 執行測試，預期 FAIL。
- [ ] **Step 3:** 實作。
- [ ] **Step 4:** 測試 PASS。
- [ ] **Step 5: Commit**：`feat(studio): PXA1 audio pack and MP3 probe`

---

### Task 5: 韌體從 PXA1 播放音樂，移除 SPIFFS

**Files:**
- Create: `main/pxa.h`、`main/pxa.cpp`
- Test: `test/host/test_pxa.cpp`、`test/host/fixtures/sample.pxa`（由 Task 4 的 Python 產生：2 個靜音 MP3，名稱是「a.mp3」和「歌.mp3」）
- Modify:
  - `main/mp3_player.cpp`
  - `main/CMakeLists.txt`：
    - 移除 `spiffs`
    - 加入 `esp_partition`
    - 用 `add_custom_command` 呼叫 IDF 的 python 執行 `tools/pack_audio.py assets/audio build/audio.pxa`
    - 再用 `esptool_py_flash_to_partition(flash "audio" build/audio.pxa)`
  - `sdkconfig.defaults`：移除 `CONFIG_SPIFFS_OBJ_NAME_LEN`
- Delete: `media::make_playlist` 不再使用，連同它的測試一起刪除。保留 `pcm_format_supported`。

**Interfaces:**
- Consumes：Task 3 的 `crc32::update`
- Produces：
  - `bool pxa::parse_header(const uint8_t* hdr, size_t len, pxa::Header* out, const char** err)`
  - `bool pxa::parse_table(const uint8_t* table, size_t len, uint16_t count, uint32_t partition_size, std::vector<pxa::Entry>* out, const char** err)`
  - `pxa::Entry` 的欄位：`std::string name`、`uint32_t offset`、`uint32_t length`
  - `parse_table` 要檢查：每個 offset 加 length 不超過 partition_size，而且不能跟標頭或檔案表重疊
- `mp3_player` 的流程：
  1. 讀標頭和檔案表，並用 `esp_partition_read` 分段計算整個 payload 的 CRC。
  2. CRC 不符時記錄 `pxa: crc mismatch`，播放器停止。
  3. 播放時用 `esp_partition_read` 每次讀 2048 bytes，取代原本的 `fread`。
  4. 解碼迴圈完全沿用。

- [ ] **Step 1: 寫失敗測試**
  - `test_parse_fixture`：2 筆、名稱正確、offset 對齊 4
  - 拒絕 bad magic
  - 拒絕版本 2
  - 拒絕 file_count=65
  - 拒絕 entry 超出分割區
  - 拒絕 entry 跟檔案表重疊
  - 拒絕檔名沒有 NUL 也沒有填滿
  - 確認舊的 SPIFFS 內容會被拒絕：用 magic 不符的資料模擬
- [ ] **Step 2:** 執行測試，預期 FAIL。
- [ ] **Step 3:** 實作，並把 `menu.sh` 的 host 測試換成 pxa、pxg 和 crc32。
- [ ] **Step 4:** 測試 PASS，`idf.py build` 成功，`assets/audio` 的 Main Theme 被打包成 `build/audio.pxa`。
- [ ] **Step 5: 實機驗證**
  - 燒錄後 Main Theme 照常循環播放。
  - 燒一個 file_count=0 的 PXA，預期 log 顯示 "no tracks"，GIF 照常顯示。
- [ ] **Step 6: Commit**：`feat: play MP3 from PXA1 audio partition, drop SPIFFS`

---

### Task 6: 讀取板子狀態並決定燒錄計畫

**Files:**
- Modify: `studio/pixelboard/board.py`、`studio/pixelboard/firmware.py`
- Test: `studio/tests/test_board_plan.py`

**Interfaces:**
- Consumes：Task 1 的 `Flasher.read`，Task 3 的 `pxg.pack`，Task 4 的 `pxa.pack`
- Produces：
  - `board.AppInfo = NamedTuple(project: str, version: str)`
  - `board.parse_app_desc(image_head: bytes) -> AppInfo|None`
    - `image_head` 是從 0x10000 讀出的 256 bytes
    - `esp_app_desc_t` 位在 image 的 +0x20，magic 是 0xABCD5432
    - version 在 +0x10，是 char[32]；project_name 在 +0x30，是 char[32]
  - `board.BoardState = NamedTuple(app: AppInfo|None, partition_table: bytes)`：`partition_table` 是 0x8000 處讀出的 0xC00 bytes
  - `board.plan_flash(state: BoardState, bundled: dict[str, bytes], bundled_app: AppInfo, gif: bytes|None, audio: bytes|None) -> board.FlashPlan`
  - `FlashPlan` 的欄位：
    - `kind`：`"first_install"`、`"update_firmware"` 或 `"update_content"`
    - `regions: list[tuple[int, bytes]]`
    - `notes: list[str]`：給使用者看的中文提示

**決定規則：**

| 板子狀態 | kind | 寫入的區塊 |
|---|---|---|
| `app is None` 或 `project != "pixel-board"` | `first_install` | bootloader@0x0、partition_table@0x8000、app@0x10000，加上 gif、audio |
| project 相同，但 version 不同，**或者**分割表跟內附的不同 | `update_firmware` | 分割表有差異時才寫 partition_table，加上 app、gif、audio |
| 其他 | `update_content` | 只寫 gif、audio |

- gif 寫入 0x210000，audio 寫入 0x310000。
- `gif` 或 `audio` 是 None 時，不寫那個區塊。
- `audio is None` 時，`notes` 加一句「保留板子上原本的音樂」。
- `audio` 有值時，`notes` 加一句「板子上的音樂會被這份清單取代」。
- `update_content` 而且 gif 和 audio 都是 None 時，丟出 `BoardError("沒有要燒錄的內容")`。

- [ ] **Step 1: 寫失敗測試**，每種情況一個：
  - 出廠 demo：project 是 `"factory_demo"` → `first_install`
  - 全空的 flash：0xFF → `first_install`
  - 同版本 → `update_content`，只有 gif 區
  - 不同版本 → `update_firmware`，有 app 但沒有 bootloader
  - 舊分割表（factory 3M） → `update_firmware`，而且 regions 包含 0x8000
  - audio 是 None 時有「保留」的提示
  - 什麼都沒有 → `BoardError`

  另外加 `test_parse_app_desc`：用實際的 `build/pixel-board.bin` 前 256 bytes，預期得到 `("pixel-board", "1.0.0")`。如果 build 還沒跑，就把這個檔案的前 256 bytes 複製成 fixture。
- [ ] **Step 2:** 執行測試，預期 FAIL。
- [ ] **Step 3:** 實作。`firmware.load_images()` 回傳的內容加上 `bundled_app_info()`。
- [ ] **Step 4:** 測試 PASS。
- [ ] **Step 5: Commit**：`feat(studio): detect board state and plan flash regions`

---

### Task 7: 完整設定面板

**Files:**
- Modify: `studio/pixelboard/app.py`、`studio/pixelboard/ui/*`
- Test: `studio/tests/test_api.py`（直接呼叫 `StudioApi`，不開視窗）

**Interfaces:**
- Consumes：Task 2 的 `gifproc.load`、`render`、`Layout`，Task 3 的 `pxg.pack`，Task 4 的 `pxa.pack`、`validate_track`，Task 6 的 `plan_flash`，Task 1 的 `Flasher`
- Produces，`StudioApi` 給 JS 呼叫的方法，全部回傳可 JSON 序列化的 dict。失敗時回傳 `{"ok": False, "message": user_message}`，不會丟例外到 JS：
  - `open_gif(path: str)`：成功回傳 `{"ok", "name", "frames": int}`
  - `render_preview(layout: dict)`：回傳 `{"ok", "width", "height", "frames": [{"rgb_b64": str, "ms": int}], "brightness"}`
  - `add_tracks(paths: list[str])`：回傳 `{"ok", "tracks": [{"name","size","rate","channels"}], "used": int, "capacity": int, "rejected": [{"name","message"}]}`
  - `remove_track(index: int)`、`move_track(src: int, dst: int)`：回傳的格式跟 `add_tracks` 相同
  - `track_data_url(index: int)`：回傳 `{"ok", "url": "data:audio/mpeg;base64,..."}`，給 JS 試聽用
  - `find_board()`：沿用 Task 1
  - `flash(layout: dict, port: str|None)`：依序做這些事：
    1. 讀取板子狀態
    2. 用 `plan_flash` 決定要寫哪些區塊
    3. 呼叫 JS 的 `window.studio.confirm(kind, notes)`，讓使用者確認
    4. 寫入
    5. hard reset

    回傳 `{"ok", "message"}`。
- **UI 區塊**：
  - 拖放區：用 pywebview 6 提供的 `File.pywebviewFullPath` 取得路徑，實作前先用 context7 確認；另外也提供「選擇檔案」按鈕
  - 預覽 canvas：
    - 每個 LED 畫成一顆圓點
    - 面板之間留接縫
    - 依每格 ms 播放
    - 可以暫停、逐格看
    - 亮度用 CIE 1931 的查表把顏色調暗，模擬面板函式庫的行為
  - 控制項：
    - 面板數：1 或 2
    - 版面：整張或每塊一份，只有 chain=2 時能選每塊一份
    - X/Y 位置：方向鍵，或在預覽上拖曳
    - 縮放滑桿
    - 亮度滑桿：10–128
    - 左右對調
  - 音樂清單：
    - 可以加入、刪除、上下移動
    - 可以試聽
    - 顯示容量條
  - 燒錄按鈕、進度條和白話訊息
  - 救援說明：錯誤訊息含「BOOT」時，顯示一張簡單的板子示意圖（inline SVG），標出 BOOT 和 RESET 鍵的位置，並列出三個步驟。
  - 調整參數時，用 debounce 200ms 再呼叫 `render_preview`
  - `layout` dict 的 key：`chain`、`split`、`offset_x`、`offset_y`、`scale`、`brightness`、`swap`。前五個轉成 `gifproc.Layout`，`brightness` 和 `swap` 交給 `pxg.pack`。

- [ ] **Step 1: 寫失敗測試**
  - `test_open_gif_then_preview_size`：chain=2 時，width=128
  - `test_preview_bytes_equal_flash_bytes`：`render_preview` 解碼後的 rgb，等於 flash 時 `pxg.pack` 用的 frames。flash 的部分用 mock Flasher 取得寫入 0x210000 的內容，再用 `pxg.unpack` 比對
  - `test_add_tracks_rejects_bad_with_reason`
  - `test_capacity_reported`
  - `test_flash_without_board_message`：`find_board_ports` 回傳空的時，message 含「找不到板子」
  - `test_flash_port_busy_message`：mock Flasher 丟出 `BoardError`，message 原樣回傳
- [ ] **Step 2:** 執行測試，預期 FAIL。
- [ ] **Step 3:** 實作 API 和 UI。
- [ ] **Step 4:** 測試 PASS。
- [ ] **Step 5: 實機驗證**，WSL 先 detach 板子：
  1. 用 `menu.sh` 的 "Build Studio exe" 產生 exe。
  2. 拖進 eyes.gif，選每塊一份，預覽應該跟目前板子上的畫面一致。
  3. 加入 Main Theme 和一個 96kHz 檔案，預期 96kHz 被拒絕並說明原因。
  4. 按燒錄，預期是 `update_content`，只寫 gif 和 audio，幾十秒內完成。
  5. 換 megaman.gif，用整張模式，按燒錄後面板正確顯示。
- [ ] **Step 6: Commit**：`feat(studio): full settings panel with preview, music and flashing`

---

### Task 8: 打包收尾和說明文件

**Files:**
- Modify: `studio/PixelBoard-Studio.spec`（加 icon，選用）、`README.md`

**`README.md` 要寫兩段**，都用中文：
- 「給使用者」：
  1. 下載 `PixelBoard-Studio.exe`
  2. 接上板子
  3. 點兩下打開，拖進 GIF 和 MP3
  4. 按燒錄

  另外附上常見錯誤和按 BOOT 鍵救援的方法。
- 「給開發者」：`menu.sh` 的各選項，以及怎麼重新產生 exe。

- [ ] **Step 1: 乾淨環境測試**：在 Windows 用另一個沒有 Python 的帳號，或在 Windows Sandbox 裡，點兩下 exe，預期可以開啟。如果做不到乾淨環境，就記錄「未在乾淨環境驗證」並告訴使用者。
- [ ] **Step 2:** 用一塊抹除過的板子（`esptool erase_flash`）走一次首次安裝，預期 `first_install`，燒完後顯示 GIF，並播放加入的音樂。
- [ ] **Step 3:** 全部測試、`idf.py build`、`menu.sh` 選項 6 都通過。
- [ ] **Step 4: Commit**：`docs: README for PixelBoard Studio`。**不要 push**，回報使用者。
