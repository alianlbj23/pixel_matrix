// pixel-board: 64x32 HUB75 panel + speaker.
// Shows full red, green and blue for 1 s each, "HELLO" with a startup jingle, then loops
// the GIF from gif_anim_data.cpp forever while MP3s from assets/audio/ loop in the background.
#include <memory>
#include <new>

#include "ESP32-HUB75-MatrixPanel-I2S-DMA.h"
#include "audio.h"
#include "board_config.h"
#include "mp3_player.h"
#include "esp_log.h"
#include "font5x7.h"
#include "gif_anim.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"

namespace {

constexpr const char *TAG = "pixel-board";
constexpr TickType_t kColorHoldTicks = pdMS_TO_TICKS(1000);
constexpr char kHelloText[] = "HELLO";

// Must stay alive for the whole program: DMA keeps refreshing the panel from its buffer.
std::unique_ptr<MatrixPanel_I2S_DMA> g_panel;

bool init_panel() {
    HUB75_I2S_CFG::i2s_pins pins = {kPinR1, kPinG1, kPinB1, kPinR2, kPinG2, kPinB2, kPinA,
                                    kPinB,  kPinC,  kPinD,  kPinE,  kPinLat, kPinOe, kPinClk};
    HUB75_I2S_CFG cfg(kPanelWidth, kPanelHeight, kPanelChain, pins);
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
    g_panel->setBrightness8(kPanelBrightness);
    g_panel->clearScreen();
    ESP_LOGI(TAG, "HUB75 init OK: %ux%u chain=%u clkphase=%s brightness=%u/255",
             static_cast<unsigned>(kPanelWidth), static_cast<unsigned>(kPanelHeight),
             static_cast<unsigned>(kPanelChain), kPanelClkPhase ? "true" : "false",
             static_cast<unsigned>(kPanelBrightness));
    return true;
}

void show_color(const char *name, uint8_t r, uint8_t g, uint8_t b) {
    ESP_LOGI(TAG, "fill %s", name);
    g_panel->fillScreenRGB888(r, g, b);
    vTaskDelay(kColorHoldTicks);
}

void show_text_centered(const char *text) {
    g_panel->clearScreen();
    const int x = (kPanelWidth - font5x7::text_width(text)) / 2;
    const int y = (kPanelHeight - font5x7::kGlyphHeight) / 2;
    const int unsupported = font5x7::draw_text(text, x, y, [](int px, int py) {
        g_panel->drawPixelRGB888(static_cast<int16_t>(px), static_cast<int16_t>(py), 255, 255, 255);
    });
    if (unsupported > 0) {
        ESP_LOGW(TAG, "text \"%s\": %d character(s) not in font, left blank", text, unsupported);
    }
    ESP_LOGI(TAG, "showing text \"%s\"", text);
}

void draw_gif_frame(const gif_anim::Frame &frame) {
    const uint8_t *p = frame.rgb;
    for (int y = 0; y < gif_anim::kHeight; ++y) {
        for (int x = 0; x < gif_anim::kWidth; ++x, p += 3) {
            g_panel->drawPixelRGB888(static_cast<int16_t>(x), static_cast<int16_t>(y), p[0], p[1], p[2]);
        }
    }
}

// Never returns unless the generated data does not match the panel.
void play_gif_forever() {
    if (gif_anim::kFrameCount == 0) {
        ESP_LOGE(TAG, "GIF: no frames in gif_anim_data.cpp, nothing to play");
        return;
    }
    if (gif_anim::kWidth != kPanelWidth || gif_anim::kHeight != kPanelHeight) {
        ESP_LOGE(TAG, "GIF: data is %ux%u but panel is %ux%u; re-run menu.sh \"Convert GIF\"",
                 static_cast<unsigned>(gif_anim::kWidth), static_cast<unsigned>(gif_anim::kHeight),
                 static_cast<unsigned>(kPanelWidth), static_cast<unsigned>(kPanelHeight));
        return;
    }
    ESP_LOGI(TAG, "GIF: playing %u frame(s)", static_cast<unsigned>(gif_anim::kFrameCount));
    for (;;) {
        for (size_t i = 0; i < gif_anim::kFrameCount; ++i) {
            draw_gif_frame(gif_anim::kFrames[i]);
            TickType_t ticks = pdMS_TO_TICKS(gif_anim::kFrames[i].duration_ms);
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
    if (!init_panel()) {
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
    play_gif_forever();
}
