#include "mp3_player.h"

#include <dirent.h>

#include <cstdint>
#include <cstdio>
#include <memory>
#include <string>
#include <vector>

#include "audio.h"
#include "esp_audio_dec_default.h"
#include "esp_audio_simple_dec_default.h"
#include "esp_log.h"
#include "esp_spiffs.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "media.h"

namespace mp3_player {

namespace {

constexpr const char *TAG = "mp3";
constexpr const char *kMountPoint = "/audio";
constexpr const char *kPartitionLabel = "audio";
constexpr size_t kReadSize = 2048;
constexpr size_t kInitialOutSize = 4608;   // one MPEG-1 Layer III frame, 16-bit stereo
constexpr size_t kMaxOutSize = 64 * 1024;  // refuse absurd decoder buffer requests
constexpr uint32_t kTaskStack = 8192;
constexpr UBaseType_t kTaskPriority = 5;
constexpr BaseType_t kTaskCore = 1;  // app_main (GIF loop) runs on core 0

bool g_started = false;

struct FileCloser {
    void operator()(FILE *f) const { fclose(f); }
};
struct DecoderCloser {
    void operator()(void *h) const { esp_audio_simple_dec_close(h); }
};

bool mount() {
    esp_vfs_spiffs_conf_t conf = {};
    conf.base_path = kMountPoint;
    conf.partition_label = kPartitionLabel;
    conf.max_files = 2;
    conf.format_if_mount_failed = false;  // never wipe the music partition
    esp_err_t err = esp_vfs_spiffs_register(&conf);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "mount \"%s\" FAILED (%s); was the audio partition flashed?", kPartitionLabel,
                 esp_err_to_name(err));
        return false;
    }
    size_t total = 0, used = 0;
    err = esp_spiffs_info(kPartitionLabel, &total, &used);
    if (err == ESP_OK) {
        ESP_LOGI(TAG, "mounted %s: %u KB used of %u KB", kMountPoint, static_cast<unsigned>(used / 1024),
                 static_cast<unsigned>(total / 1024));
    } else {
        ESP_LOGW(TAG, "esp_spiffs_info failed (%s)", esp_err_to_name(err));
    }
    return true;
}

std::vector<std::string> list_playlist() {
    DIR *dir = opendir(kMountPoint);
    if (dir == nullptr) {
        ESP_LOGE(TAG, "opendir %s failed", kMountPoint);
        return {};
    }
    std::vector<std::string> names;
    while (const dirent *e = readdir(dir)) {
        names.emplace_back(e->d_name);
    }
    closedir(dir);
    std::vector<std::string> playlist = media::make_playlist(names);
    if (playlist.size() != names.size()) {
        ESP_LOGW(TAG, "%u file(s) skipped (not .mp3, hidden, or name longer than %u bytes)",
                 static_cast<unsigned>(names.size() - playlist.size()), static_cast<unsigned>(media::kMaxNameLen));
    }
    return playlist;
}

// Decodes one file and streams it to the speaker. Returns false (logged) on any error.
bool play_file(const std::string &name) {
    const std::string path = std::string(kMountPoint) + "/" + name;
    std::unique_ptr<FILE, FileCloser> file(fopen(path.c_str(), "rb"));
    if (!file) {
        ESP_LOGE(TAG, "%s: cannot open", name.c_str());
        return false;
    }

    esp_audio_simple_dec_cfg_t cfg = {};
    cfg.dec_type = ESP_AUDIO_SIMPLE_DEC_TYPE_MP3;
    cfg.use_frame_dec = false;
    esp_audio_simple_dec_handle_t raw_handle = nullptr;
    esp_audio_err_t ret = esp_audio_simple_dec_open(&cfg, &raw_handle);
    if (ret != ESP_AUDIO_ERR_OK) {
        ESP_LOGE(TAG, "%s: decoder open failed (%d)", name.c_str(), static_cast<int>(ret));
        return false;
    }
    std::unique_ptr<void, DecoderCloser> dec(raw_handle);

    std::vector<uint8_t> in(kReadSize);
    std::vector<uint8_t> out(kInitialOutSize);
    bool format_ready = false;
    uint64_t pcm_bytes = 0;
    uint32_t bytes_per_sec = 0;

    for (;;) {
        const size_t n = fread(in.data(), 1, in.size(), file.get());
        if (n == 0 && ferror(file.get())) {
            ESP_LOGE(TAG, "%s: read error", name.c_str());
            return false;
        }
        esp_audio_simple_dec_raw_t raw = {};
        raw.buffer = in.data();
        raw.len = static_cast<uint32_t>(n);
        raw.eos = n < in.size();
        // Do not overwrite `in` while raw.len > 0: the decoder may still reference it.
        while (raw.len > 0) {
            esp_audio_simple_dec_out_t frame = {};
            frame.buffer = out.data();
            frame.len = static_cast<uint32_t>(out.size());
            ret = esp_audio_simple_dec_process(dec.get(), &raw, &frame);
            if (ret == ESP_AUDIO_ERR_BUFF_NOT_ENOUGH) {
                if (frame.needed_size == 0 || frame.needed_size > kMaxOutSize) {
                    ESP_LOGE(TAG, "%s: decoder wants %u byte output buffer", name.c_str(),
                             static_cast<unsigned>(frame.needed_size));
                    return false;
                }
                out.resize(frame.needed_size);
                continue;
            }
            if (ret != ESP_AUDIO_ERR_OK) {
                ESP_LOGE(TAG, "%s: decode error (%d)", name.c_str(), static_cast<int>(ret));
                return false;
            }
            if (frame.decoded_size > 0) {
                if (!format_ready) {
                    esp_audio_simple_dec_info_t info = {};
                    ret = esp_audio_simple_dec_get_info(dec.get(), &info);
                    if (ret != ESP_AUDIO_ERR_OK ||
                        !media::pcm_format_supported(info.sample_rate, info.channel, info.bits_per_sample)) {
                        ESP_LOGE(TAG, "%s: unsupported format %u Hz x%u %u-bit (ret %d)", name.c_str(),
                                 static_cast<unsigned>(info.sample_rate), static_cast<unsigned>(info.channel),
                                 static_cast<unsigned>(info.bits_per_sample), static_cast<int>(ret));
                        return false;
                    }
                    if (!audio::set_format(info.sample_rate, info.channel)) return false;
                    bytes_per_sec = info.sample_rate * info.channel * 2;
                    format_ready = true;
                    ESP_LOGI(TAG, "%s: %u Hz, %u ch", name.c_str(), static_cast<unsigned>(info.sample_rate),
                             static_cast<unsigned>(info.channel));
                }
                if (!audio::write_pcm(frame.buffer, frame.decoded_size)) return false;
                pcm_bytes += frame.decoded_size;
            } else if (raw.consumed == 0) {
                if (raw.eos) break;  // trailing bytes that are not a full frame
                ESP_LOGE(TAG, "%s: decoder made no progress", name.c_str());
                return false;
            }
            raw.len -= raw.consumed;
            raw.buffer += raw.consumed;
        }
        if (raw.eos) break;
    }

    if (pcm_bytes == 0) {
        ESP_LOGE(TAG, "%s: no audio decoded (not an MP3?)", name.c_str());
        return false;
    }
    ESP_LOGI(TAG, "%s: done, %u s", name.c_str(), static_cast<unsigned>(pcm_bytes / bytes_per_sec));
    return true;
}

// Plays the playlist forever. Returns only when nothing can be played.
void run() {
    for (;;) {
        const std::vector<std::string> playlist = list_playlist();
        if (playlist.empty()) {
            ESP_LOGW(TAG, "no .mp3 files in %s; put them in assets/audio/ and flash again", kMountPoint);
            return;
        }
        ESP_LOGI(TAG, "playlist: %u file(s)", static_cast<unsigned>(playlist.size()));
        size_t played = 0;
        for (const std::string &name : playlist) {
            ESP_LOGI(TAG, "playing %s", name.c_str());
            if (play_file(name)) ++played;
        }
        if (played == 0) {
            ESP_LOGE(TAG, "none of the %u file(s) could be played; stopping", static_cast<unsigned>(playlist.size()));
            return;
        }
    }
}

void task_main(void *) {
    run();
    ESP_LOGW(TAG, "player task exiting");
    vTaskDelete(nullptr);
}

}  // namespace

bool start() {
    if (g_started) return true;
    esp_audio_err_t ret = esp_audio_dec_register_default();
    if (ret != ESP_AUDIO_ERR_OK) {
        ESP_LOGE(TAG, "esp_audio_dec_register_default failed (%d)", static_cast<int>(ret));
        return false;
    }
    ret = esp_audio_simple_dec_register_default();
    if (ret != ESP_AUDIO_ERR_OK) {
        ESP_LOGE(TAG, "esp_audio_simple_dec_register_default failed (%d)", static_cast<int>(ret));
        esp_audio_dec_unregister_default();
        return false;
    }
    if (!mount()) {
        esp_audio_simple_dec_unregister_default();
        esp_audio_dec_unregister_default();
        return false;
    }
    if (xTaskCreatePinnedToCore(task_main, "mp3", kTaskStack, nullptr, kTaskPriority, nullptr, kTaskCore) != pdPASS) {
        ESP_LOGE(TAG, "cannot create player task (out of memory)");
        esp_vfs_spiffs_unregister(kPartitionLabel);
        esp_audio_simple_dec_unregister_default();
        esp_audio_dec_unregister_default();
        return false;
    }
    g_started = true;
    return true;
}

}  // namespace mp3_player
