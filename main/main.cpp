// pixel-board: 1..kMaxPanels chained 64x32 HUB75 panels + speaker.
// Shows full red, green and blue for 1 s each, "HELLO" with a startup jingle, then loops the
// PXG animation from the `gif` partition forever (or "NO GIF" if it is missing/invalid) while
// MP3s loop in the background. Panel count and brightness come from the PXG header.
// "PXB IDENTIFY <n>" on the USB Serial/JTAG console shows each panel's number and "up" arrow for 5 s.
#include <cstdio>
#include <memory>
#include <new>

#include "ESP32-HUB75-MatrixPanel-I2S-DMA.h"
#include "audio.h"
#include "board_config.h"
#include "cmd.h"
#include "driver/usb_serial_jtag.h"
#include "driver/usb_serial_jtag_vfs.h"
#include "esp_heap_caps.h"
#include "esp_log.h"
#include "esp_partition.h"
#include "esp_private/gdma.h"
#include "font5x7.h"
#include "freertos/FreeRTOS.h"
#include "freertos/semphr.h"
#include "freertos/task.h"
#include "identify.h"
#include "mp3_player.h"
#include "pxg.h"

namespace {

constexpr const char *TAG = "pixel-board";
constexpr TickType_t kColorHoldTicks = pdMS_TO_TICKS(1000);
constexpr TickType_t kIdentifyHoldTicks = pdMS_TO_TICKS(5000);
constexpr TickType_t kSerialReadTicks = pdMS_TO_TICKS(1000);
constexpr char kHelloText[] = "HELLO";
constexpr char kNoGifText[] = "NO GIF";
constexpr uint8_t kGifPartitionSubtype = 0x40;

constexpr uint32_t kGifTaskStack = 4096;
constexpr UBaseType_t kGifTaskPriority = 2;
constexpr uint32_t kCmdTaskStack = 4096;
constexpr UBaseType_t kCmdTaskPriority = 3;
constexpr BaseType_t kDisplayCore = 0;  // mp3 player runs on core 1
constexpr uint32_t kUsbTxBufferSize = 1024;
constexpr uint32_t kUsbRxBufferSize = 256;

// Exposes the protected DMA bus so the panel can be torn down completely (see destroy_panel()).
class Hub75Panel : public MatrixPanel_I2S_DMA {
public:
    using MatrixPanel_I2S_DMA::MatrixPanel_I2S_DMA;
    Bus_Parallel16 &bus() { return dma_bus; }
};

// The library (3.0.14, ESP32-S3) neither stops nor deletes its GDMA channel when the panel object
// is destroyed, and the channel keeps the LCD peripheral claimed, so a second panel object could
// not connect its own channel. Bus_Parallel16::dma_chan is private; this standard explicit-
// instantiation idiom yields a pointer to it (access checks do not apply to explicit
// instantiation arguments) without patching the managed component.
struct BusDmaChan {
    using type = gdma_channel_handle_t Bus_Parallel16::*;
};
BusDmaChan::type bus_dma_chan_member(BusDmaChan);
template <typename Tag, typename Tag::type Member>
struct PrivateMember {
    friend typename Tag::type bus_dma_chan_member(Tag) { return Member; }
};
template struct PrivateMember<BusDmaChan, &Bus_Parallel16::dma_chan>;

gdma_channel_handle_t &dma_chan_of(Hub75Panel &panel) { return panel.bus().*bus_dma_chan_member(BusDmaChan{}); }

// Guards g_panel, g_canvas_width and g_resting_text. DMA keeps refreshing the panel from the
// object's buffers, so g_panel must stay alive while anything is shown.
SemaphoreHandle_t g_display_mutex = nullptr;
std::unique_ptr<Hub75Panel> g_panel;
int g_canvas_width = kPanelWidth;      // 64 * chain of the current panel object
const char *g_resting_text = nullptr;  // static text to redraw after identify (nullptr: none)

// The configuration the display returns to after identify; set once in app_main.
uint8_t g_chain = kPanelChain;
uint8_t g_brightness = kPanelBrightness;

pxg::View g_gif{};  // points into the gif partition mapping, kept for the program's lifetime

class DisplayLock {
public:
    DisplayLock() { xSemaphoreTake(g_display_mutex, portMAX_DELAY); }
    ~DisplayLock() { xSemaphoreGive(g_display_mutex); }
    DisplayLock(const DisplayLock &) = delete;
    DisplayLock &operator=(const DisplayLock &) = delete;
};

// Maps the `gif` partition and validates its PXG animation. The mapping is kept for the
// lifetime of the program because `out` points into it.
bool load_gif(pxg::View *out) {
    const esp_partition_t *part = esp_partition_find_first(
        ESP_PARTITION_TYPE_DATA, static_cast<esp_partition_subtype_t>(kGifPartitionSubtype), "gif");
    if (part == nullptr) {
        ESP_LOGE(TAG, "pxg: no \"gif\" partition (old partition table?)");
        return false;
    }
    const void *ptr = nullptr;
    esp_partition_mmap_handle_t handle;
    const esp_err_t err = esp_partition_mmap(part, 0, part->size, ESP_PARTITION_MMAP_DATA, &ptr, &handle);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "pxg: mmap of gif partition failed (%s)", esp_err_to_name(err));
        return false;
    }
    const char *why = nullptr;
    if (!pxg::parse(static_cast<const uint8_t *>(ptr), part->size, out, &why)) {
        ESP_LOGE(TAG, "pxg: %s", why);
        esp_partition_munmap(handle);
        return false;
    }
    ESP_LOGI(TAG, "pxg: %u frame(s), chain=%u brightness=%u swap=%d", static_cast<unsigned>(out->frame_count),
             static_cast<unsigned>(out->chain), static_cast<unsigned>(out->brightness), out->swap ? 1 : 0);
    return true;
}

void log_dma_heap(const char *when) {
    constexpr uint32_t caps = MALLOC_CAP_INTERNAL | MALLOC_CAP_DMA;
    ESP_LOGI(TAG, "heap %s: internal DMA free=%u largest=%u", when,
             static_cast<unsigned>(heap_caps_get_free_size(caps)),
             static_cast<unsigned>(heap_caps_get_largest_free_block(caps)));
}

// Caller holds g_display_mutex. Stops DMA and frees everything the panel object owns.
void destroy_panel() {
    if (!g_panel) return;
    gdma_channel_handle_t &chan = dma_chan_of(*g_panel);
    if (chan != nullptr) {
        g_panel->bus().dma_transfer_stop();
        esp_err_t err = gdma_disconnect(chan);
        if (err != ESP_OK) ESP_LOGE(TAG, "HUB75 teardown: gdma_disconnect failed (%s)", esp_err_to_name(err));
        err = gdma_del_channel(chan);
        if (err != ESP_OK) ESP_LOGE(TAG, "HUB75 teardown: gdma_del_channel failed (%s)", esp_err_to_name(err));
        chan = nullptr;
    }
    g_panel.reset();  // frees the DMA frame buffers and descriptors
}

// Caller holds g_display_mutex. Replaces the panel object with one for `chain` panels.
bool create_panel(uint8_t chain, uint8_t brightness) {
    if (chain < 1 || chain > kMaxPanels) {
        ESP_LOGE(TAG, "HUB75 init FAILED: chain %u out of range 1..%d", static_cast<unsigned>(chain), kMaxPanels);
        return false;
    }
    destroy_panel();
    log_dma_heap("before HUB75 init");
    HUB75_I2S_CFG::i2s_pins pins = {kPinR1, kPinG1, kPinB1, kPinR2, kPinG2, kPinB2, kPinA,
                                    kPinB,  kPinC,  kPinD,  kPinE,  kPinLat, kPinOe, kPinClk};
    HUB75_I2S_CFG cfg(kPanelWidth, kPanelHeight, chain, pins);
    cfg.driver = HUB75_I2S_CFG::SHIFTREG;
    cfg.clkphase = kPanelClkPhase;

    g_panel.reset(new (std::nothrow) Hub75Panel(cfg));
    if (!g_panel) {
        ESP_LOGE(TAG, "HUB75 init FAILED: out of memory creating panel object");
        return false;
    }
    dma_chan_of(*g_panel) = nullptr;  // left uninitialised by the library until begin() allocates it
    if (!g_panel->begin()) {
        ESP_LOGE(TAG, "HUB75 init FAILED: begin() returned false (DMA buffer or GPIO setup), chain=%u",
                 static_cast<unsigned>(chain));
        destroy_panel();
        log_dma_heap("after failed HUB75 init");
        return false;
    }
    g_panel->setBrightness8(brightness);
    g_panel->clearScreen();
    g_canvas_width = kPanelWidth * chain;
    ESP_LOGI(TAG, "HUB75 init OK: %ux%u chain=%u clkphase=%s brightness=%u/255",
             static_cast<unsigned>(kPanelWidth), static_cast<unsigned>(kPanelHeight),
             static_cast<unsigned>(chain), kPanelClkPhase ? "true" : "false", static_cast<unsigned>(brightness));
    log_dma_heap("after HUB75 init");
    return true;
}

void show_color(const char *name, uint8_t r, uint8_t g, uint8_t b) {
    ESP_LOGI(TAG, "fill %s", name);
    {
        DisplayLock lock;
        if (g_panel) g_panel->fillScreenRGB888(r, g, b);
    }
    vTaskDelay(kColorHoldTicks);
}

// Caller holds g_display_mutex.
void draw_text_centered(const char *text) {
    if (!g_panel) return;
    g_panel->clearScreen();
    const int x = (g_canvas_width - font5x7::text_width(text)) / 2;
    const int y = (kPanelHeight - font5x7::kGlyphHeight) / 2;
    const int unsupported = font5x7::draw_text(text, x, y, [](int px, int py) {
        g_panel->drawPixelRGB888(static_cast<int16_t>(px), static_cast<int16_t>(py), 255, 255, 255);
    });
    if (unsupported > 0) {
        ESP_LOGW(TAG, "text \"%s\": %d character(s) not in font, left blank", text, unsupported);
    }
}

// Shows text until something else is drawn; it is redrawn after identify.
void show_text_centered(const char *text) {
    DisplayLock lock;
    g_resting_text = text;
    draw_text_centered(text);
    ESP_LOGI(TAG, "showing text \"%s\"", text);
}

// Caller holds g_display_mutex.
void draw_gif_frame(const pxg::View &gif, uint16_t index) {
    const uint8_t *p = gif.frames + static_cast<size_t>(index) * gif.frame_bytes;
    const int width = pxg::kPanelW * gif.chain;
    for (int y = 0; y < pxg::kPanelH; ++y) {
        for (int x = 0; x < width; ++x, p += 3) {
            g_panel->drawPixelRGB888(static_cast<int16_t>(pxg::panel_x(x, gif.chain, gif.swap)),
                                     static_cast<int16_t>(y), p[0], p[1], p[2]);
        }
    }
}

[[noreturn]] void gif_task(void *) {
    ESP_LOGI(TAG, "GIF: playing %u frame(s)", static_cast<unsigned>(g_gif.frame_count));
    for (;;) {
        for (uint16_t i = 0; i < g_gif.frame_count; ++i) {
            {
                DisplayLock lock;
                if (g_panel) draw_gif_frame(g_gif, i);
            }
            const TickType_t ticks = pdMS_TO_TICKS(pxg::duration_ms(g_gif, i));
            vTaskDelay(ticks > 0 ? ticks : 1);
        }
    }
}

// Command replies share stdout with ESP_LOG; one printf call is written as a whole line.
void reply_ok_identify(int n) {
    std::printf("PXB OK IDENTIFY %d\n", n);
    std::fflush(stdout);
}

void reply_error(const char *reason) {
    std::printf("PXB ERR %s\n", reason);
    std::fflush(stdout);
}

// Caller holds g_display_mutex. Back to the normal panel and whatever static text was showing;
// the GIF task redraws its next frame on its own.
void restore_display() {
    if (!create_panel(g_chain, g_brightness)) {
        ESP_LOGE(TAG, "identify: could not restore the %u-panel display", static_cast<unsigned>(g_chain));
        return;
    }
    if (g_resting_text != nullptr) draw_text_centered(g_resting_text);
}

void run_identify(int n) {
    DisplayLock lock;
    ESP_LOGI(TAG, "identify: showing %d panel(s) for 5 s", n);
    if (!create_panel(static_cast<uint8_t>(n), g_brightness)) {
        reply_error("panel init failed");
        restore_display();
        return;
    }
    identify::draw(n, [](int x, int y, uint8_t r, uint8_t g, uint8_t b) {
        g_panel->drawPixelRGB888(static_cast<int16_t>(x), static_cast<int16_t>(y), r, g, b);
    });
    reply_ok_identify(n);
    vTaskDelay(kIdentifyHoldTicks);
    restore_display();
    ESP_LOGI(TAG, "identify: done, display restored to chain=%u", static_cast<unsigned>(g_chain));
}

void handle_line(const char *line) {
    const cmd::Result r = cmd::parse(line);
    switch (r.kind) {
        case cmd::Kind::None:
            break;
        case cmd::Kind::Error:
            ESP_LOGW(TAG, "command rejected: %s", r.reason);
            reply_error(r.reason);
            break;
        case cmd::Kind::Identify:
            run_identify(r.n);
            break;
    }
}

// Reads "\n"-terminated lines from USB Serial/JTAG. Lines longer than cmd::kMaxLineLength are
// discarded; if they look like a PXB command the sender gets an error reply.
[[noreturn]] void cmd_task(void *) {
    char line[cmd::kMaxLineLength + 1];
    size_t len = 0;
    bool overflow = false;
    uint8_t buf[64];
    for (;;) {
        const int got = usb_serial_jtag_read_bytes(buf, sizeof(buf), kSerialReadTicks);
        for (int i = 0; i < got; ++i) {
            const char c = static_cast<char>(buf[i]);
            if (c == '\n') {
                line[len] = '\0';
                if (!overflow) {
                    handle_line(line);
                } else if (cmd::parse(line).kind != cmd::Kind::None) {
                    ESP_LOGW(TAG, "command rejected: line longer than %u characters",
                             static_cast<unsigned>(cmd::kMaxLineLength));
                    reply_error("line too long");
                }
                len = 0;
                overflow = false;
            } else if (len < cmd::kMaxLineLength) {
                line[len++] = c;
            } else {
                overflow = true;
            }
        }
    }
}

// Installs the USB Serial/JTAG driver (needed to read the console), keeps ESP_LOG/stdout on it
// and starts the command task. Problems are logged; the display keeps working without commands.
void start_cmd_task() {
    if (!usb_serial_jtag_is_driver_installed()) {
        usb_serial_jtag_driver_config_t cfg = {};
        cfg.tx_buffer_size = kUsbTxBufferSize;
        cfg.rx_buffer_size = kUsbRxBufferSize;
        const esp_err_t err = usb_serial_jtag_driver_install(&cfg);
        if (err != ESP_OK) {
            ESP_LOGE(TAG, "USB Serial/JTAG driver install failed (%s), commands disabled", esp_err_to_name(err));
            return;
        }
    }
    usb_serial_jtag_vfs_use_driver();
    if (xTaskCreatePinnedToCore(cmd_task, "cmd", kCmdTaskStack, nullptr, kCmdTaskPriority, nullptr, kDisplayCore) !=
        pdPASS) {
        ESP_LOGE(TAG, "could not start command task, commands disabled");
        return;
    }
    ESP_LOGI(TAG, "USB commands ready (PXB IDENTIFY <1..%d>)", kMaxPanels);
}

// Rising C-major arpeggio. Audio problems are logged but never stop the display.
// Returns true if audio is usable.
bool play_startup_jingle() {
    struct Note {
        float freq_hz;
        uint32_t ms;
    };
    static constexpr Note kJingle[] = {{523.25f, 120}, {659.25f, 120}, {783.99f, 120}, {1046.50f, 300}};
    if (!audio::init()) {
        ESP_LOGW(TAG, "audio unavailable, skipping startup jingle");
        return false;
    }
    ESP_LOGI(TAG, "playing startup jingle");
    for (const Note &n : kJingle) {
        if (!audio::play_tone(n.freq_hz, n.ms)) {
            ESP_LOGW(TAG, "startup jingle aborted");
            break;
        }
    }
    return true;
}

}  // namespace

extern "C" void app_main(void) {
    g_display_mutex = xSemaphoreCreateMutex();
    if (g_display_mutex == nullptr) {
        ESP_LOGE(TAG, "out of memory creating the display mutex");
        return;
    }
    const bool have_gif = load_gif(&g_gif);
    g_chain = have_gif ? g_gif.chain : kPanelChain;
    g_brightness = have_gif ? g_gif.brightness : kPanelBrightness;
    {
        DisplayLock lock;
        if (!create_panel(g_chain, g_brightness)) return;
    }
    start_cmd_task();
    show_color("red", 255, 0, 0);
    show_color("green", 0, 255, 0);
    show_color("blue", 0, 0, 255);
    show_text_centered(kHelloText);
    if (play_startup_jingle() && !mp3_player::start()) {
        ESP_LOGW(TAG, "MP3 player unavailable, continuing without music");
    }
    vTaskDelay(kColorHoldTicks);
    if (have_gif) {
        {
            DisplayLock lock;
            g_resting_text = nullptr;
        }
        if (xTaskCreatePinnedToCore(gif_task, "gif", kGifTaskStack, nullptr, kGifTaskPriority, nullptr,
                                    kDisplayCore) == pdPASS) {
            return;
        }
        ESP_LOGE(TAG, "could not start the GIF task");
    } else {
        ESP_LOGW(TAG, "no valid animation in the gif partition; flash one with PixelBoard Studio");
    }
    show_text_centered(kNoGifText);
}
