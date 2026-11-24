// pixel-board: 1 or 2 chained 64x32 HUB75 panels + speaker.
// Shows full red, green and blue for 1 s each, "HELLO" with a startup jingle, then loops the
// PXG1 animation from the `gif` partition forever (or "NO GIF" if it is missing/invalid) while
// MP3s loop in the background. Panel count and brightness come from the PXG1 header.
#include <memory>
#include <new>

#include "ESP32-HUB75-MatrixPanel-I2S-DMA.h"
#include "audio.h"
#include "board_config.h"
#include "mp3_player.h"
#include "esp_log.h"
#include "font5x7.h"
#include "esp_partition.h"
#include "pxg.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"

namespace {

constexpr const char *TAG = "pixel-board";
constexpr TickType_t kColorHoldTicks = pdMS_TO_TICKS(1000);
constexpr char kHelloText[] = "HELLO";
constexpr char kNoGifText[] = "NO GIF";
constexpr uint8_t kGifPartitionSubtype = 0x40;

// Must stay alive for the whole program: DMA keeps refreshing the panel from its buffer.
std::unique_ptr<MatrixPanel_I2S_DMA> g_panel;
int g_canvas_width = kPanelWidth;  // 64 * chain, set by init_panel()

// Maps the `gif` partition and validates its PXG1 animation. The mapping is kept for the
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

bool init_panel(uint8_t chain, uint8_t brightness) {
    HUB75_I2S_CFG::i2s_pins pins = {kPinR1, kPinG1, kPinB1, kPinR2, kPinG2, kPinB2, kPinA,
                                    kPinB,  kPinC,  kPinD,  kPinE,  kPinLat, kPinOe, kPinClk};
    HUB75_I2S_CFG cfg(kPanelWidth, kPanelHeight, chain, pins);
    cfg.driver = HUB75_I2S_CFG::SHIFTREG;
    cfg.clkphase = kPanelClkPhase;

    g_panel.reset(new (std::nothrow) MatrixPanel_I2S_DMA(cfg));
    if (!g_panel) {
        ESP_LOGE(TAG, "HUB75 init FAILED: out of memory creating panel object");
        return false;
    }
    if (!g_panel->begin()) {
        ESP_LOGE(TAG, "HUB75 init FAILED: begin() returned false (DMA buffer or GPIO setup)");
        g_panel.reset();
        return false;
    }
    g_panel->setBrightness8(brightness);
    g_panel->clearScreen();
    g_canvas_width = kPanelWidth * chain;
    ESP_LOGI(TAG, "HUB75 init OK: %ux%u chain=%u clkphase=%s brightness=%u/255",
             static_cast<unsigned>(kPanelWidth), static_cast<unsigned>(kPanelHeight),
             static_cast<unsigned>(chain), kPanelClkPhase ? "true" : "false", static_cast<unsigned>(brightness));
    return true;
}

void show_color(const char *name, uint8_t r, uint8_t g, uint8_t b) {
    ESP_LOGI(TAG, "fill %s", name);
    g_panel->fillScreenRGB888(r, g, b);
    vTaskDelay(kColorHoldTicks);
}

void show_text_centered(const char *text) {
    g_panel->clearScreen();
    const int x = (g_canvas_width - font5x7::text_width(text)) / 2;
    const int y = (kPanelHeight - font5x7::kGlyphHeight) / 2;
    const int unsupported = font5x7::draw_text(text, x, y, [](int px, int py) {
        g_panel->drawPixelRGB888(static_cast<int16_t>(px), static_cast<int16_t>(py), 255, 255, 255);
    });
    if (unsupported > 0) {
        ESP_LOGW(TAG, "text \"%s\": %d character(s) not in font, left blank", text, unsupported);
    }
    ESP_LOGI(TAG, "showing text \"%s\"", text);
}

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

[[noreturn]] void play_gif_forever(const pxg::View &gif) {
    ESP_LOGI(TAG, "GIF: playing %u frame(s)", static_cast<unsigned>(gif.frame_count));
    for (;;) {
        for (uint16_t i = 0; i < gif.frame_count; ++i) {
            draw_gif_frame(gif, i);
            const TickType_t ticks = pdMS_TO_TICKS(pxg::duration_ms(gif, i));
            vTaskDelay(ticks > 0 ? ticks : 1);
        }
    }
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
    pxg::View gif{};
    const bool have_gif = load_gif(&gif);
    if (!init_panel(have_gif ? gif.chain : kPanelChain, have_gif ? gif.brightness : kPanelBrightness)) {
        return;
    }
    show_color("red", 255, 0, 0);
    show_color("green", 0, 255, 0);
    show_color("blue", 0, 0, 255);
    show_text_centered(kHelloText);
    if (play_startup_jingle() && !mp3_player::start()) {
        ESP_LOGW(TAG, "MP3 player unavailable, continuing without music");
    }
    vTaskDelay(kColorHoldTicks);
    if (have_gif) {
        play_gif_forever(gif);
    }
    ESP_LOGW(TAG, "no valid animation in the gif partition; flash one with PixelBoard Studio");
    show_text_centered(kNoGifText);
}
