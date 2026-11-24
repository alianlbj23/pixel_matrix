# Multi-Panel Arrangement Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** PixelBoard Studio 支援 1–8 塊任意擺放、可旋轉的 64x32 面板，並提供「辨識面板」、拖曳吸附的排列編輯器、燒錄鎖定和停止燒錄，最後交付使用者可直接用的 exe。

**Architecture:** 排列換算全部在 Python 端。`layout.py` 負責面板幾何、吸附、預設排列、canvas 與排線順序緩衝區的雙向換算。`gifproc` 把 GIF 畫在排列外框（canvas）上，再換算成排線順序寫進 PXG v2。韌體只依 N 塊面板依序輸出，另外多一個 USB 文字指令 `PXB IDENTIFY <N>`。

**Tech Stack:** 沿用現有：ESP-IDF v5.5（C++）、Python（pywebview 6.2.1、esptool 5.6.0、pyserial 3.5、Pillow）、unittest、g++ host 測試。**不新增任何套件。**

**Spec:** `docs/superpowers/specs/2026-11-24-multi-panel-design.md`（延伸 `docs/superpowers/specs/2026-11-24-gif-studio-design.md`）

**承接的工作：** 前一份計畫 `docs/superpowers/plans/2026-11-24-pixelboard-studio.md` 的 Task 0–6 已經完成。Task 7 的程式碼已 commit，還沒做 Windows 實測。Task 8 還沒做。這兩個都併入本計畫的 Task 9。

## Global Constraints

- 只在分支 `feature/studio` 上工作，每個 task 完成就 commit。**不准 push。**
- 不准新增相依套件。
- 面板規格：
  - 單塊 64x32
  - 面板數 N 是 1..8，常數 `MAX_PANELS = 8`
  - 旋轉只能是 {0, 90, 180, 270}，代表從正面看，面板被**順時針**轉了幾度
- 新的分割表：

  | 名稱 | 類型 | 位置 | 大小 |
  |---|---|---|---|
  | nvs | data, nvs | 0x9000 | 0x6000 |
  | phy_init | data, phy | 0xf000 | 0x1000 |
  | factory | app | 0x10000 | 2M |
  | gif | data, 0x40 | 0x210000 | 3M |
  | audio | data, 0x41 | 0x510000 | 10M |

- 容量常數：
  - `pxg.MAX_BYTES = 3 * 1024 * 1024`
  - `pxa.CAPACITY = 10 * 1024 * 1024`
  - `board.GIF_OFFSET = 0x210000`
  - `board.AUDIO_OFFSET = 0x510000`
- 格數上限：`max_frames(N) = min(64, (pxg.MAX_BYTES - 4096) // (64 * N * 32 * 3))`。N=8 時是 63 格，N≤7 時是 64 格。這個值以程式計算為準，不要寫死。
- 吸附門檻是 8 個 LED 像素。面板不能重疊，放開時如果重疊，就退回原位。
- 辨識面板：
  - 指令是 `PXB IDENTIFY <N>\n`
  - 成功回覆 `PXB OK IDENTIFY <N>`
  - 錯誤回覆 `PXB ERR <reason>`
  - 程式等回覆最多 1.5 秒
  - 開 port 時 **DTR=True**（WSL 實測：DTR=False 會讓板子重開）
  - 韌體顯示 5 秒後恢復原畫面
- 停止燒錄：寫入 gif 或 audio 區時，每寫完 64KB 檢查一次停止要求；寫入 bootloader、分割表或 app 時不能停止。
- 使用者看到的文字一律用繁體中文白話；程式碼、log、識別名稱用英文。
- 錯誤處理：不准吞例外；序列埠和燒錄都要設 timeout；外部輸入（JS 傳來的 dict、layout.json、序列埠回覆、PXG 資料）都要先驗證。
- 完成的定義：
  - `menu.sh` 的 do_tests 全部 OK，包含所有 host 測試和 Studio unit tests
  - `idf.py build` 沒有新的 warning
- Python 測試指令：`.venv-studio/bin/python -m unittest discover -s studio/tests -t studio`
- host C++ 測試的編譯方式沿用 `menu.sh` 的寫法。

## Review Focus

1. **板子還是舊韌體，或是全新的板子時按「辨識」**：預期等 1.5 秒逾時後，顯示「請先按『安裝/更新韌體』」，不能卡住，也不能顯示錯誤代碼（Task 6 的 `test_identify_no_reply`）。
2. **使用者把面板數從 4 改成 2，但目前的排列或 GIF 格數已經不合**：預期移除編號最大的面板；格數超過時，在預覽和燒錄前用白話提示（Task 1 的 `test_resize_removes_highest`、Task 6 的 `test_frame_limit_message`）。
3. **layout.json 損壞或被手動改壞**（重疊、N=9、旋轉 45 度、不是 JSON）：預期回到預設的 1 塊，記 log，不會讓程式崩潰（Task 1 的 `test_load_corrupt_falls_back`）。
4. **燒錄中再按一次燒錄、拖放檔案、或改設定**：預期被鎖定的狀態擋下；API 也要擋，不只靠 UI 變灰（Task 6 的 `test_edits_rejected_while_flashing`）。
5. **在寫入 app 的途中按停止**：預期不會中斷，等 app 寫完、進入 gif 區後才停（Task 6 的 `test_stop_deferred_during_firmware`）。

---

## File Structure

```
partitions.csv                       修改：gif 3M、audio 10M @0x510000
main/board_config.h                  修改：kMaxPanels = 8
main/pxg.h / pxg.cpp                 修改：接受版本 2（chain 1..8，跳過排列表）
main/cmd.h / cmd.cpp                 新增：USB 指令解析（純 C++，可在 host 測試）
main/font5x7.cpp                     修改：加入數字 0-9 和上箭頭字元 '^'
main/identify.h / identify.cpp       新增：畫出辨識畫面（純 C++，透過回呼畫點，可在 host 測試）
main/main.cpp                        修改：面板由 display mutex 保護；GIF 改成獨立 task；新增 serial command task
test/host/test_cmd.cpp, test_identify.cpp, fixtures/sample_v2.pxg
studio/pixelboard/layout.py          新增：Panel、Arrangement、幾何、吸附、預設、chain 換算、json
studio/pixelboard/gifproc.py         修改：Placement 和 render 改成畫在 canvas 上
studio/pixelboard/pxg.py             修改：版本 2 和排列表；max_frames()
studio/pixelboard/pxa.py, board.py   修改：容量和位移
studio/pixelboard/flasher.py         修改：write_chunked 支援停止
studio/pixelboard/serialcmd.py       新增：identify(port)，透過 pyserial 傳指令
studio/pixelboard/app.py             修改：排列 API、辨識、只安裝韌體、停止燒錄、鎖定
studio/pixelboard/ui/*               修改：排列編輯器、鎖定、停止
tools/pack_gif.py, menu.sh           修改：--layout，以及 assets/layout.json
README.md                            修改：使用說明
```

---

### Task 1: 排列模型 `layout.py`

**Files:**
- Create: `studio/pixelboard/layout.py`
- Test: `studio/tests/test_layout.py`

**Interfaces:**
- Produces（全部是純函式；不合法的輸入一律丟 `LayoutError(user_message)`）：
  - `Panel = NamedTuple(index: int, x: int, y: int, rotation: int)`
  - `Arrangement = NamedTuple(panels: tuple[Panel, ...])`
    - 依 `index` 排序
    - `n` 屬性 = 面板數
  - `panel_size(rotation) -> (w, h)`：0 和 180 是 (64, 32)，90 和 270 是 (32, 64)
  - `validate(arr) -> None`，檢查：
    - 1 ≤ n ≤ 8
    - index 剛好是 0..n-1
    - 旋轉值合法
    - 座標在 -4096..4096
    - 沒有重疊
  - `normalize(arr) -> Arrangement`：整體平移，讓外框左上角在 (0, 0)
  - `bounds(arr) -> (w, h)`：normalize 之後的外框大小
  - `snap(arr, index, x, y, threshold=8) -> Arrangement`：把第 index 塊移到 (x, y)。如果它的某個邊離其他面板的某個邊在 threshold 以內，就貼齊；如果平行方向的邊緣也在 threshold 以內，也一起對齊。結果會重疊的話，回傳原本的 arr。
  - `resize(arr, n) -> Arrangement`：
    - 增加：新面板放在目前外框的最右邊，y 是 0，旋轉 0
    - 減少：移除 index 最大的面板
  - `preset(name, n) -> Arrangement`，`name` 可以是：
    - `"row"`：一排
    - `"column"`：直的疊起來
    - `"grid"`：每列 ceil(sqrt(n)) 塊，由左到右、由上到下
    - `"snake"`：跟 grid 一樣，但偶數列（第 2、4… 列）從右到左排，旋轉 180
  - `grid_shape(arr) -> (rows, cols, order) | None`：
    - 所有面板旋轉相同
    - 不同的 y 值有 rows 個，不同的 x 值有 cols 個
    - rows × cols = n，每個 (y, x) 組合剛好一塊
    - `order` 是依列優先排列的 panel index list
  - `canvas_to_panel(p: Panel, cx, cy) -> (lx, ly) | None`（canvas 座標，已 normalize）和 `chain_frame(canvas_rgb, cw, ch, arr) -> bytes`（長度 64*n*32*3）
    - 第 i 塊面板的本地座標 (lx, ly)，lx 是 0..63，ly 是 0..31；ly=0 是面板的「上方」，也就是辨識箭頭指的方向
    - 本地座標對應到 canvas 座標的公式：
      ```
      rot   0: (x+lx,      y+ly)
      rot  90: (x+31-ly,   y+lx)
      rot 180: (x+63-lx,   y+31-ly)
      rot 270: (x+ly,      y+63-lx)
      ```
  - `canvas_frame(chain_rgb, arr) -> (bytes, cw, ch)`：反向換算，不屬於任何面板的像素是黑色。用來驗證，也給預覽使用。
  - `to_dict(arr)`、`from_dict(d) -> Arrangement`：`from_dict` 會呼叫 validate
  - `load(path) -> Arrangement`：檔案不存在或資料不合法時，回傳 `preset("row", 1)`，並記 warning
  - `save(path, arr)`

- [ ] **Step 1: 寫失敗測試** `test_layout.py`
  - `test_panel_size`
  - `test_validate_rejects`：n=0、n=9、index 重複、旋轉 45、兩塊重疊
  - `test_presets`：
    - `preset("row",3)`：x 是 0/64/128
    - `preset("column",2)`：y 是 0/32
    - `preset("grid",4)`：2×2
    - `preset("snake",4)`：第 2 列是 index 2 在 x=64、index 3 在 x=0，而且兩塊都是 rot 180
  - `test_snap_right_edge`：row 2，把 1 號移到 (70, 5)，結果是 (64, 0)
  - `test_snap_below`：把 1 號移到 (3, 36)，結果是 (0, 32)
  - `test_snap_overlap_reverts`：把 1 號移到 (10, 0)，回傳原本的 arr
  - `test_resize_adds_right_and_removes_highest`
  - `test_grid_shape_with_gaps`：兩塊在 (0,0) 和 (80,0)，結果是 (1, 2, [0, 1])；L 形是 None；旋轉混合是 None
  - `test_chain_roundtrip_all_rotations`：每種旋轉放一塊，用隨機的 canvas 做 `chain_frame` 再做 `canvas_frame`，結果要等於「只保留面板內像素」的 canvas
  - `test_rotation_90_mapping`：rot 90 時，本地 (0,0) 對應到 canvas (31,0)，本地 (63,31) 對應到 (0,63)
  - `test_load_corrupt_falls_back`：分別寫入「不是 JSON」、`{"panels": []}` 和重疊的資料，都會得到 1 塊
  - `test_save_load_roundtrip`
- [ ] **Step 2:** 執行測試，預期 `ModuleNotFoundError`。
- [ ] **Step 3: 實作 `layout.py`。**
- [ ] **Step 4:** 測試 PASS。
- [ ] **Step 5: Commit**：`feat(studio): panel arrangement model with snapping and chain mapping`

---

### Task 2: gifproc 改成畫在 canvas 上，並且支援 grid 分割

**Files:**
- Modify: `studio/pixelboard/gifproc.py`、`studio/tests/test_gifproc.py`

**Interfaces:**
- Consumes：Task 1 的 `bounds`、`normalize`、`grid_shape`、`panel_size`
- Produces：
  - `gifproc.Placement = dataclass(mode: str = "whole", offset_x: int = 0, offset_y: int = 0, scale: float = 1.0)`
    - `mode` 是 `"whole"` 或 `"split"`
    - 取代舊的 `Layout`，舊的要刪掉
  - `gifproc.render(src, placement, arr) -> list[(canvas_rgb: bytes, ms)]`：canvas 大小是 `layout.bounds(arr)`
  - **whole**：在 canvas 上等比縮放、置中，乘上 scale 後再平移；超出 canvas 的部分裁掉。
  - **split**：
    - 需要 `grid_shape(arr)` 不是 None，否則丟 `GifError("這種排法只能用「整張」")`
    - 取可見範圍的聯集，切成 rows × cols 份
    - 第 (r, c) 份放進 order[r*cols+c] 那塊面板的範圍（依旋轉後的寬高），所有面板用相同比例置中，再套用 offset 和 scale，裁切在該面板範圍內
  - `gifproc.max_frames` 沿用，但 load 時不檢查面板相關的格數，那部分由 Task 3 的 `pxg.max_frames(n)` 處理。

- [ ] **Step 1: 改寫測試**
  - 舊的 `Layout(chain=2, split=True)` 測試改成 `render(src, Placement("split"), preset("row", 2))`，期望的像素值不變。原本 128 寬的位置換算到 canvas 上剛好一樣，因為 row 2 的 canvas 就是 128x32。
  - 新增 `test_split_grid_2x2`：GIF 分成四個象限，分別是紅、綠、藍、白，放進 `preset("grid",4)`，每塊面板中心的顏色要對應正確
  - 新增 `test_split_with_gap`：兩塊在 x=0 和 x=80，canvas 是 144 寬；x=64..79 全黑，兩塊面板各有內容
  - 新增 `test_split_irregular_rejected`
  - 新增 `test_whole_on_column`：`preset("column",2)` 的 canvas 是 64x64
- [ ] **Step 2:** 執行測試，預期 FAIL。
- [ ] **Step 3:** 實作，同時更新呼叫端：`app.py` 和 `tools/pack_gif.py` 改用新的 API，暫時用 `preset("row", chain)`。
- [ ] **Step 4:** 全部 Python 測試 PASS。
- [ ] **Step 5: Commit**：`refactor(studio): gifproc renders onto an arrangement canvas`

---

### Task 3: PXG 版本 2（Python 和 C++）

**PXG v2**：header 跟 v1 相同，差別如下：
- `version = 2`
- `chain` 範圍 1..8
- flags 的 bit1 = split（只是記錄，韌體不使用）

Payload 依序是：
1. durations（u16 × count），補 0 對齊到 4 bytes
2. **排列表**：n × 8 bytes，每筆是 `<hhHH`：x、y、rotation、0
3. frames（每格 `64*n*32*3` bytes）

v1 的 payload 沒有排列表。

**Files:**
- Modify: `studio/pixelboard/pxg.py`、`main/pxg.h`、`main/pxg.cpp`、`studio/tests/test_pxg.py`、`test/host/test_pxg.cpp`
- Create: `test/host/fixtures/sample_v2.pxg`（n=3，panels 是 (0,0,0)、(64,0,0)、(64,32,180)，durations 是 {100, 200}，frame0 第一個像素是 (1,2,3)）

**Interfaces:**
- Python：
  - `pxg.pack(frames, arr: layout.Arrangement, brightness: int, split: bool) -> bytes`：一律寫 v2，`frames` 是 chain 格式的 bytes
  - `pxg.unpack(data) -> Animation`，`Animation` 有這些欄位：
    - `chain`
    - `brightness`
    - `swap`（v1）
    - `split`
    - `panels`（v2 是 `list[(x,y,rot)]`，v1 是 `[]`）
    - `frames`
  - `pxg.max_frames(n) -> int`
  - `pxg.MAX_BYTES = 3 MiB`
- C++：
  - `pxg::parse` 同時接受 v1（chain 1..2）和 v2（chain 1..8，跳過排列表）
  - `View` 新增 `uint8_t version`
  - `kMaxChain = 8`

- [ ] **Step 1: 寫失敗測試**
  - Python：
    - `test_v2_roundtrip_with_panels`
    - `test_v1_still_unpacks`：用目前 `assets/gif.pxg` 的前幾個 bytes，或用 Task 3 之前的 pack 程式產生的 v1 bytes
    - `test_chain_8_ok_9_rejected`
    - `test_max_frames`：`max_frames(1) == 64`、`max_frames(8) == 63`，並且斷言 `max_frames(8) * 8*6144 <= MAX_BYTES - 4096`
    - `test_too_many_frames_for_chain`
  - C++：
    - `test_parse_v2_fixture`：chain=3、durations、frames[0]=(1,2,3)
    - `test_v1_fixture_still_parses`：沿用 `sample.pxg`
    - `test_rejects_v2_chain_9`
    - `test_rejects_v3`
- [ ] **Step 2:** 執行測試，預期 FAIL。
- [ ] **Step 3:** 實作，用 Python 產生 fixture。同時更新 `app.py` 和 `pack_gif.py` 的呼叫，在 Task 7 之前先用 `preset("row", chain)` 和 `split`。
- [ ] **Step 4:** 全部測試 PASS。
- [ ] **Step 5: Commit**：`feat: PXG v2 with up to 8 panels and arrangement table`

---

### Task 4: 分割表和容量

**Files:**
- Modify: `partitions.csv`、`studio/pixelboard/pxa.py`（`CAPACITY`）、`studio/pixelboard/board.py`（`AUDIO_OFFSET`）、`studio/pixelboard/ui/*`（容量文字從 12MB 改成 10MB，改用 API 回傳的值，不要寫死）、`studio/tests/fixtures/partition-table.bin`（重新從 build 複製）、`sdkconfig.defaults` 註解、相關測試的常數
- Test: 既有測試更新，`test_capacity_constant` 改成 10 MiB；`test_board_plan` 的 audio offset 改成 0x510000

- [ ] **Step 1:** 先改測試的期望值，執行後預期 FAIL。
- [ ] **Step 2:** 改常數和 `partitions.csv`，`idf.py build` 成功，並把新的分割表複製成 fixture。
- [ ] **Step 3:** 全部測試 PASS。
- [ ] **Step 4: 實機驗證**：`idf.py flash` 之後，log 顯示 `pxg: ... chain=2` 和 `mp3: playlist: 1 file(s)`，播放 Main Theme。
- [ ] **Step 5: Commit**：`feat: 3 MB gif partition, 10 MB audio`

---

### Task 5: 韌體支援最多 8 塊面板和辨識指令

**Files:**
- Create: `main/cmd.h`、`main/cmd.cpp`、`main/identify.h`、`main/identify.cpp`、`test/host/test_cmd.cpp`、`test/host/test_identify.cpp`
- Modify: `main/font5x7.cpp`、`test/host/test_font.cpp`、`main/main.cpp`、`main/board_config.h`、`main/CMakeLists.txt`、`menu.sh`（加入兩個 host 測試）

**Interfaces:**
- `cmd::Result cmd::parse(const char* line)`：
  - `Result` 的欄位：`kind`（`None`、`Identify` 或 `Error`）、`n`、`reason`（const char*）
  - 接受 `PXB IDENTIFY <1..8>`，前後可以有空白和 `\r`
  - 其他以 `PXB` 開頭的指令回 `Error`
  - 不是以 `PXB` 開頭的回 `None`，靜默忽略
- `void identify::draw(int n, const std::function<void(int x, int y, uint8_t r, uint8_t g, uint8_t b)>& set_pixel)`：
  - 第 i 塊面板（x 偏移 64i）的畫面：
    1. 用 3 倍大的字型，在本地 (6, 5) 畫出編號 i+1
    2. 在本地 x=40..56 畫一個指向 ly=0 的實心箭頭（`'^'` 字元 3 倍大）
    3. 邊框畫 1 px 的藍色 (0, 80, 255)
- `main.cpp`：
  - 新增 `SemaphoreHandle_t g_display_mutex`。所有使用 `g_panel` 的地方都要先拿到 mutex。
  - GIF 播放改成獨立 task，每格畫圖前先拿 mutex。
  - 新增 `cmd_task`：
    - 安裝 USB Serial/JTAG driver，並把 console 切換到 driver。實作前用 context7 查 ESP-IDF v5.5 的 `usb_serial_jtag_driver_install` 和對應的 VFS 函式名稱。
    - 逐行讀取，每行最多 64 字元。
    - 收到 Identify 時，依序：
      1. 拿 mutex
      2. 用 n 塊面板重新建立 panel
      3. 執行 `identify::draw`
      4. 回覆 `PXB OK IDENTIFY n`
      5. 等 5 秒
      6. 用原本的 chain 和亮度重新建立 panel
      7. 放開 mutex，GIF 繼續播放
    - 收到 Error 時回 `PXB ERR <reason>`。
  - `board_config.h` 新增 `kMaxPanels = 8`。

- [ ] **Step 1: 寫失敗測試**
  - `test_cmd`：
    - 合法的 1、8、`"  PXB IDENTIFY 3\r"`
    - Error 的情況：0、9、`abc`、`PXB FOO`
    - None 的情況：`hello`、空字串
  - `test_identify`：n=2，取樣檢查：
    - (0,0) 是藍邊框
    - 面板 2 的邊框在 x=64
    - 編號區域有點亮的像素
    - 每塊面板的箭頭尖端在 ly 最小的位置
  - `test_font`：`glyph('0'..'9')` 和 `glyph('^')` 不是 nullptr
- [ ] **Step 2:** 執行測試，預期 FAIL。
- [ ] **Step 3:** 實作。`idf.py build` 成功，沒有新的 warning。
- [ ] **Step 4: 實機驗證**（WSL，板子接著 2 塊面板）：
  - 用 pyserial 開 port（`dtr=True`、`rts=False`），送出 `PXB IDENTIFY 2`，預期收到 `PXB OK IDENTIFY 2`，log 沒有重開，5 秒後 GIF 恢復。
  - 送出 `PXB IDENTIFY 8`，預期 OK。實物只有 2 塊，前兩塊顯示 1 和 2，記錄 DMA 記憶體 log 和是否閃爍，然後恢復。
  - 用 `pack_gif` 產生 chain=8 的 PXG 並燒錄，記錄 `I2S-DMA` 的 refresh 和 free heap log。如果 init 失敗，把 `kMaxPanels` 降到實測可行的值，記錄 ruling，同時更新 Python 的 `MAX_PANELS`。
- [ ] **Step 5: Commit**：`feat: up to 8 panels and PXB IDENTIFY command`

---

### Task 6: Studio 後端：排列 API、辨識、只裝韌體、停止燒錄、鎖定

**Files:**
- Create: `studio/pixelboard/serialcmd.py`、`studio/tests/test_serialcmd.py`
- Modify: `studio/pixelboard/flasher.py`、`studio/pixelboard/app.py`、`studio/tests/test_api.py`、`studio/tests/test_board_ports.py`

**Interfaces:**
- `serialcmd.identify(port: str, n: int, open_serial=serial.Serial, timeout=1.5) -> str`：
  - 開 port 時 dtr=True、rts=False
  - 寫入指令，逐行讀取，直到讀到 `PXB OK`、`PXB ERR` 或逾時
  - 成功回傳中文訊息
  - 失敗丟 `BoardError`：
    - 逾時：「這塊板子還沒有新版韌體，請先按『安裝/更新韌體』」
    - ERR：「板子回報錯誤：<reason>」
    - 開 port 失敗：走 `to_board_error`
- `Flasher.write_chunked(regions, stop: threading.Event, chunk=65536) -> bool`：
  - offset < 0x210000 的區塊一律整塊寫完，不檢查 stop
  - 其他區塊每 64KB 寫一次並檢查 stop；被停止時回傳 False
- `StudioApi` 新增的方法（全部回傳 dict，不丟例外）：
  - `get_arrangement()`：回傳 `{"ok", "arrangement": to_dict, "grid": bool, "max_frames": int, "power_warning": bool}`
  - `set_panel_count(n)`
  - `apply_preset(name)`
  - `move_panel(index, x, y)`：使用 `snap`
  - `rotate_panel(index)`：+90 度；如果會重疊，就不變並回傳原排列和提示訊息
  - 上面任何一個成功後都要呼叫 `layout.save`，路徑是 `%LOCALAPPDATA%\PixelBoard-Studio\layout.json`，可以注入
- `render_preview(placement: dict)`：
  - dict 的 key：`mode`、`offset_x`、`offset_y`、`scale`、`brightness`
  - 回傳 `{"ok", "width", "height", "panels": [{"index","x","y","w","h","rotation"}], "frames": [...], "brightness", "max_frames"}`
  - 格數超過 `max_frames(n)` 時，回傳 `ok=False`，message 寫「目前 N 塊面板最多 M 格，這個 GIF 有 K 格」
- `identify(port)`
- `prepare_flash(placement, port, firmware_only=False)`：`firmware_only=True` 時 gif 和 audio 都是 None。只有在 kind 是 `update_content` 時，「沒有內容」才算錯誤。
- `confirm_flash()`：使用 `write_chunked`，進度事件帶 `stoppable`（bool）。被停止時，先 hard reset，再回傳 `{"ok": False, "stopped": True, "message": "已停止燒錄：板子上的 GIF/音樂可能不完整，請重新燒錄"}`。
- `stop_flash()`：設定 Event，不要拿 `_lock`。
- **鎖定**：`_flashing` 為 True 時（從 prepare 成功到 confirm 或 cancel 結束），以下方法一律回傳 `_fail("燒錄中，請先停止燒錄")`：
  - `open_gif`
  - `add_tracks`、`remove_track`、`move_track`
  - 所有排列相關的 API
  - `render_preview`
  - `identify`

- [ ] **Step 1: 寫失敗測試**
  - `test_identify_ok`：fake serial 回覆 `PXB OK IDENTIFY 2`
  - `test_identify_no_reply`：逾時訊息含「安裝/更新韌體」
  - `test_identify_err`
  - `test_identify_opens_with_dtr`：fake serial 記錄 dtr 是 True
  - `test_write_chunked_stops_in_gif_region`、`test_stop_deferred_during_firmware`：fake esp，write 計數
  - `test_arrangement_api_persists`：存檔的路徑是 tmp
  - `test_move_panel_snaps`
  - `test_frame_limit_message`：8 塊面板配上 30 格的 GIF
  - `test_firmware_only_plan`
  - `test_edits_rejected_while_flashing`
  - `test_preview_equals_flash_inverse`：用 `layout.canvas_frame(pxg 寫入的 chain frame)` 跟預覽 canvas 中面板內的像素比對，排列用 snake 4 塊
- [ ] **Step 2:** 執行測試，預期 FAIL。
- [ ] **Step 3:** 實作。
- [ ] **Step 4:** 全部測試 PASS。
- [ ] **Step 5: Commit**：`feat(studio): arrangement API, identify, firmware-only install, stop and lock`

---

### Task 7: UI：排列編輯器、鎖定、停止燒錄

**Files:**
- Modify: `studio/pixelboard/ui/index.html`、`app.js`、`style.css`

**行為**，對應 spec 第 2 節和第 4 節：
- **「面板排列」卡片**：
  - 控制項：`−` N `+`、預設按鈕（一排、直疊、方格、蛇形）、「辨識面板」、「安裝/更新韌體」
  - 編輯區把每塊面板畫成方塊，依旋轉顯示寬高，標上大字編號和指向 ly=0 方向的箭頭
  - 拖曳時，方塊跟著滑鼠移動（顯示半透明的幽靈方塊）；放開時呼叫 `move_panel`，用回傳的排列重畫，吸附或退回都是後端決定
  - 點一下方塊（沒有拖曳）呼叫 `rotate_panel`
  - N ≥ 3 時顯示電源提示
- **版面**：「每塊一份」只有在 `grid` 為 true 時能選，否則變灰並顯示「這種排法只能用整張」。
- **預覽**：依 `panels` 的範圍畫 LED 點；不屬於任何面板的區域不畫點，只畫深色背景；每塊面板畫一個細外框。
- **移除**：面板數 1/2 的切換按鈕和左右對調的勾選框都移除，改由排列編輯器取代。
- **燒錄中**：
  - 加上 `body.flashing` 樣式，所有 `.lockable` 控制項都 disabled，`pointer-events: none` 並且半透明
  - 拖放的檔案在 JS 端直接略過，並顯示「燒錄中，請先停止燒錄」
- **停止燒錄按鈕**：只在燒錄中顯示；依 `onProgress` 的 `stoppable` 參數切換 disabled，disabled 時顯示「正在寫入韌體，請稍候」。
- **進度回呼**改成 `window.studio.onProgress(stage, fraction, stoppable)`。

- [ ] **Step 1:** 實作。執行 `node --check app.js`，並檢查 id 一致（沿用之前的 grep 腳本）。
- [ ] **Step 2:** Python 測試仍然全部 PASS。
- [ ] **Step 3: Commit**：`feat(studio): arrangement editor, locked UI while flashing, stop button`

---

### Task 8: 開發者流程

**Files:**
- Modify: `tools/pack_gif.py`、`menu.sh`、`studio/tests/test_pack_gif.py`

**pack_gif 的參數：**
- `--layout <json>`：優先使用。
- 沒有給的話，用 `--chain N`，排列是 `preset("row", N)`。
- `--split`：對應 Placement mode `"split"`。

**menu.sh 的 Convert GIF：** `assets/layout.json` 存在就傳 `--layout`；否則用 `kPanelChain` 當作 `--chain`。

- [ ] **Step 1: 寫失敗測試**
  - `test_layout_file_used`：snake 4 → unpack 後 chain=4，panels 正確
  - `test_bad_layout_file_returns_1`
- [ ] **Step 2:** 實作。
- [ ] **Step 3:** 測試 PASS，menu 的 Convert GIF 實際跑一次（eyes、split、row 2）。
- [ ] **Step 4: Commit**：`feat(tools): pack_gif --layout`

---

### Task 9: 整合、exe、實機、README（承接舊計畫的 Task 7 和 Task 8）

- [ ] **Step 1:** 執行 `menu.sh` 的 do_tests，全部 OK；`idf.py build` 和 `studio/build_exe.sh` 都成功。
- [ ] **Step 2: WSL 實機**
  1. 用 StudioApi（不開視窗，真的 Flasher）走一次 `prepare_flash(firmware_only=True)` 再 confirm，預期 `update_firmware`，因為分割表變了
  2. `identify(2)` 預期 OK
  3. 用 eyes.gif 加上 row 2 split 再燒錄，預期 `update_content`
  4. 燒錄一個比較大的 audio，途中呼叫 `stop_flash`，預期 stopped，板子沒有音樂，GIF 正常
- [ ] **Step 3: 使用者 Windows 實測清單**，交給使用者執行並回報：
  1. 開啟 exe
  2. 辨識
  3. 排列吸附和旋轉
  4. eyes 每塊一份
  5. 加音樂、試聽和停止
  6. 燒錄時設定鎖住，按停止
  7. 再燒錄一次完成
- [ ] **Step 4: README.md**
  - 「給使用者」：
    - 下載 exe
    - 第一次用：安裝韌體 → 辨識 → 排列 → GIF/音樂 → 燒錄
    - 常見錯誤
    - BOOT 救援
    - 電源提示
  - 「給開發者」：`menu.sh` 各選項和 build exe 的方式
- [ ] **Step 5:** 如果做不到乾淨環境測試（沒裝 Python 的帳號或 Windows Sandbox），就記錄「未在乾淨環境驗證」。
- [ ] **Step 6: Commit**：`docs: README for PixelBoard Studio`。之後做整個分支的 final review。
