#include "audio.h"

#include "board_config.h"
#include "driver/i2c_master.h"
#include "driver/i2s_std.h"
#include "esp_codec_dev.h"
#include "esp_codec_dev_defaults.h"
#include "esp_log.h"
#include "freertos/FreeRTOS.h"
#include "tone.h"

namespace audio {

namespace {

constexpr const char *TAG = "audio";
constexpr i2c_port_t kI2cPort = I2C_NUM_0;
constexpr i2s_port_t kI2sPort = I2S_NUM_0;
constexpr float kToneAmplitude = 0.5f;
constexpr uint32_t kFadeFrames = kAudioSampleRate / 200;  // 5 ms, avoids clicks
constexpr uint32_t kMaxToneMs = 10000;
constexpr size_t kChunkFrames = 256;
constexpr uint32_t kWriteTimeoutMs = 200;  // one chunk is 16 ms at 16 kHz

i2c_master_bus_handle_t g_i2c_bus = nullptr;
i2s_chan_handle_t g_tx = nullptr;
const audio_codec_data_if_t *g_data_if = nullptr;
const audio_codec_ctrl_if_t *g_ctrl_if = nullptr;
const audio_codec_gpio_if_t *g_gpio_if = nullptr;
const audio_codec_if_t *g_codec_if = nullptr;
esp_codec_dev_handle_t g_speaker = nullptr;
bool g_ready = false;
bool g_open = false;  // codec opened with g_rate / g_channels
uint32_t g_rate = 0;
int g_channels = 0;

void release_all() {
    if (g_speaker) {
        esp_codec_dev_close(g_speaker);
        esp_codec_dev_delete(g_speaker);
        g_speaker = nullptr;
    }
    if (g_codec_if) { audio_codec_delete_codec_if(g_codec_if); g_codec_if = nullptr; }
    if (g_ctrl_if) { audio_codec_delete_ctrl_if(g_ctrl_if); g_ctrl_if = nullptr; }
    if (g_gpio_if) { audio_codec_delete_gpio_if(g_gpio_if); g_gpio_if = nullptr; }
    if (g_data_if) { audio_codec_delete_data_if(g_data_if); g_data_if = nullptr; }
    if (g_tx) { i2s_del_channel(g_tx); g_tx = nullptr; }
    if (g_i2c_bus) { i2c_del_master_bus(g_i2c_bus); g_i2c_bus = nullptr; }
    g_ready = false;
    g_open = false;
}

int open_codec(uint32_t sample_rate, int channels) {
    esp_codec_dev_sample_info_t fs = {};
    fs.sample_rate = sample_rate;
    fs.channel = static_cast<uint8_t>(channels);
    fs.bits_per_sample = 16;
    const int ret = esp_codec_dev_open(g_speaker, &fs);  // also enables I2S and the power amp
    g_open = ret == ESP_CODEC_DEV_OK;
    if (g_open) {
        g_rate = sample_rate;
        g_channels = channels;
    }
    return ret;
}

bool fail(const char *what, int code) {
    ESP_LOGE(TAG, "init FAILED at %s (code %d / %s)", what, code, esp_err_to_name(code));
    release_all();
    return false;
}

bool init_i2c() {
    i2c_master_bus_config_t cfg = {};
    cfg.i2c_port = kI2cPort;
    cfg.sda_io_num = static_cast<gpio_num_t>(kPinI2cSda);
    cfg.scl_io_num = static_cast<gpio_num_t>(kPinI2cScl);
    cfg.clk_source = I2C_CLK_SRC_DEFAULT;
    cfg.glitch_ignore_cnt = 7;
    cfg.flags.enable_internal_pullup = true;
    const esp_err_t err = i2c_new_master_bus(&cfg, &g_i2c_bus);
    return err == ESP_OK || fail("i2c_new_master_bus", err);
}

bool init_i2s() {
    i2s_chan_config_t chan_cfg = I2S_CHANNEL_DEFAULT_CONFIG(kI2sPort, I2S_ROLE_MASTER);
    chan_cfg.auto_clear = true;  // play silence when we stop feeding data
    esp_err_t err = i2s_new_channel(&chan_cfg, &g_tx, nullptr);
    if (err != ESP_OK) return fail("i2s_new_channel", err);

    i2s_std_config_t std_cfg = {};
    std_cfg.clk_cfg = I2S_STD_CLK_DEFAULT_CONFIG(kAudioSampleRate);
    std_cfg.slot_cfg = I2S_STD_PHILIPS_SLOT_DEFAULT_CONFIG(I2S_DATA_BIT_WIDTH_16BIT, I2S_SLOT_MODE_STEREO);
    std_cfg.gpio_cfg.mclk = static_cast<gpio_num_t>(kPinI2sMclk);
    std_cfg.gpio_cfg.bclk = static_cast<gpio_num_t>(kPinI2sBclk);
    std_cfg.gpio_cfg.ws = static_cast<gpio_num_t>(kPinI2sWs);
    std_cfg.gpio_cfg.dout = static_cast<gpio_num_t>(kPinI2sDout);
    std_cfg.gpio_cfg.din = I2S_GPIO_UNUSED;
    err = i2s_channel_init_std_mode(g_tx, &std_cfg);
    return err == ESP_OK || fail("i2s_channel_init_std_mode", err);
}

bool init_codec() {
    audio_codec_i2s_cfg_t i2s_cfg = {};
    i2s_cfg.port = kI2sPort;
    i2s_cfg.tx_handle = g_tx;
    i2s_cfg.rx_handle = nullptr;
    g_data_if = audio_codec_new_i2s_data(&i2s_cfg);
    if (!g_data_if) return fail("audio_codec_new_i2s_data", ESP_FAIL);

    audio_codec_i2c_cfg_t i2c_cfg = {};
    i2c_cfg.port = kI2cPort;
    i2c_cfg.addr = ES8311_CODEC_DEFAULT_ADDR;
    i2c_cfg.bus_handle = g_i2c_bus;
    g_ctrl_if = audio_codec_new_i2c_ctrl(&i2c_cfg);
    if (!g_ctrl_if) return fail("audio_codec_new_i2c_ctrl (ES8311 not answering on I2C?)", ESP_FAIL);

    g_gpio_if = audio_codec_new_gpio();
    if (!g_gpio_if) return fail("audio_codec_new_gpio", ESP_FAIL);

    es8311_codec_cfg_t es_cfg = {};
    es_cfg.ctrl_if = g_ctrl_if;
    es_cfg.gpio_if = g_gpio_if;
    es_cfg.codec_mode = ESP_CODEC_DEV_WORK_MODE_DAC;
    es_cfg.pa_pin = kPinPaEnable;
    es_cfg.pa_reverted = false;
    es_cfg.master_mode = false;
    es_cfg.use_mclk = true;
    es_cfg.hw_gain.pa_voltage = 5.0f;
    es_cfg.hw_gain.codec_dac_voltage = 3.3f;
    g_codec_if = es8311_codec_new(&es_cfg);
    if (!g_codec_if) return fail("es8311_codec_new", ESP_FAIL);

    esp_codec_dev_cfg_t dev_cfg = {};
    dev_cfg.dev_type = ESP_CODEC_DEV_TYPE_OUT;
    dev_cfg.codec_if = g_codec_if;
    dev_cfg.data_if = g_data_if;
    g_speaker = esp_codec_dev_new(&dev_cfg);
    if (!g_speaker) return fail("esp_codec_dev_new", ESP_FAIL);

    int ret = esp_codec_dev_set_out_vol(g_speaker, static_cast<float>(kAudioVolume));
    if (ret != ESP_CODEC_DEV_OK) return fail("esp_codec_dev_set_out_vol", ret);

    ret = open_codec(kAudioSampleRate, tone::kChannels);
    return ret == ESP_CODEC_DEV_OK || fail("esp_codec_dev_open", ret);
}

}  // namespace

bool init() {
    if (g_ready) return true;
    if (!init_i2c() || !init_i2s() || !init_codec()) return false;
    g_ready = true;
    ESP_LOGI(TAG, "init OK: ES8311 %u Hz 16-bit stereo, volume %d/100",
             static_cast<unsigned>(kAudioSampleRate), kAudioVolume);
    return true;
}

bool play_tone(float freq_hz, uint32_t duration_ms) {
    if (!g_ready) {
        ESP_LOGE(TAG, "play_tone: audio not initialised");
        return false;
    }
    if (duration_ms == 0 || duration_ms > kMaxToneMs) {
        ESP_LOGE(TAG, "play_tone: duration %u ms out of range 1..%u", static_cast<unsigned>(duration_ms),
                 static_cast<unsigned>(kMaxToneMs));
        return false;
    }
    const uint32_t frames = kAudioSampleRate * duration_ms / 1000;
    const uint32_t fade = kFadeFrames * 2 <= frames ? kFadeFrames : frames / 2;
    tone::SineTone gen;
    if (!gen.start(kAudioSampleRate, freq_hz, kToneAmplitude, frames, fade)) {
        ESP_LOGE(TAG, "play_tone: invalid tone %.1f Hz / %u ms", static_cast<double>(freq_hz),
                 static_cast<unsigned>(duration_ms));
        return false;
    }
    if (!set_format(kAudioSampleRate, tone::kChannels)) return false;
    static int16_t buf[kChunkFrames * tone::kChannels];
    for (;;) {
        const size_t n = gen.fill(buf, kChunkFrames);
        if (n == 0) return true;
        if (!write_pcm(buf, n * tone::kChannels * sizeof(int16_t))) return false;
    }
}

bool set_format(uint32_t sample_rate, int channels) {
    if (!g_ready) {
        ESP_LOGE(TAG, "set_format: audio not initialised");
        return false;
    }
    if (g_open && g_rate == sample_rate && g_channels == channels) return true;
    if (g_open) {
        const int ret = esp_codec_dev_close(g_speaker);
        g_open = false;
        if (ret != ESP_CODEC_DEV_OK) {
            ESP_LOGE(TAG, "set_format: esp_codec_dev_close failed (%d)", ret);
            return false;
        }
    }
    const int ret = open_codec(sample_rate, channels);
    if (ret != ESP_CODEC_DEV_OK) {
        ESP_LOGE(TAG, "set_format: cannot open codec at %u Hz x%d (%d)", static_cast<unsigned>(sample_rate),
                 channels, ret);
        return false;
    }
    ESP_LOGI(TAG, "format: %u Hz, %d channel(s), 16-bit", static_cast<unsigned>(sample_rate), channels);
    return true;
}

bool write_pcm(const void *data, size_t bytes) {
    if (!g_ready || !g_open) {
        ESP_LOGE(TAG, "write_pcm: audio not initialised or codec not open");
        return false;
    }
    if (data == nullptr || bytes == 0) return true;
    size_t written = 0;
    // Timeout scales with the chunk: real-time duration plus margin.
    const uint32_t bytes_per_sec = g_rate * static_cast<uint32_t>(g_channels) * sizeof(int16_t);
    const uint32_t timeout_ms = kWriteTimeoutMs + static_cast<uint32_t>(bytes * 1000u / bytes_per_sec);
    const esp_err_t err = i2s_channel_write(g_tx, data, bytes, &written, timeout_ms);
    if (err != ESP_OK || written != bytes) {
        ESP_LOGE(TAG, "write_pcm: I2S write failed (%s, %u/%u bytes)", esp_err_to_name(err),
                 static_cast<unsigned>(written), static_cast<unsigned>(bytes));
        return false;
    }
    return true;
}

}  // namespace audio
