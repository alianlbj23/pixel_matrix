#include "mp3_player.h"

#include <algorithm>
#include <cstdint>
#include <memory>
#include <string>
#include <vector>

#include "audio.h"
#include "crc32.h"
#include "esp_audio_dec_default.h"
#include "esp_audio_simple_dec_default.h"
#include "esp_log.h"
#include "esp_partition.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "media.h"
#include "pxa.h"

namespace mp3_player {

namespace {

constexpr const char *TAG = "mp3";
constexpr const char *kPartitionLabel = "audio";
constexpr uint8_t kAudioPartitionSubtype = 0x41;
constexpr size_t kReadSize = 2048;
constexpr size_t kCrcChunk = 4096;
constexpr size_t kInitialOutSize = 4608;   // one MPEG-1 Layer III frame, 16-bit stereo
constexpr size_t kMaxOutSize = 64 * 1024;  // refuse absurd decoder buffer requests
constexpr uint32_t kTaskStack = 8192;
constexpr UBaseType_t kTaskPriority = 5;
constexpr BaseType_t kTaskCore = 1;  // the GIF and command tasks run on core 0

bool g_started = false;
const esp_partition_t *g_part = nullptr;

struct DecoderCloser {
    void operator()(void *h) const { esp_audio_simple_dec_close(h); }
};

bool read_flash(uint32_t offset, void *dst, size_t len) {
    const esp_err_t err = esp_partition_read(g_part, offset, dst, len);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "pxa: read %u bytes at 0x%x failed (%s)", static_cast<unsigned>(len),
                 static_cast<unsigned>(offset), esp_err_to_name(err));
        return false;
    }
    return true;
}

// Reads and validates the PXA1 header, file table and payload CRC. Empty on any error (logged).
std::vector<pxa::Entry> load_playlist() {
    uint8_t hdr[pxa::kHeaderSize];
    if (!read_flash(0, hdr, sizeof(hdr))) return {};
    pxa::Header h{};
    const char *why = nullptr;
    if (!pxa::parse_header(hdr, sizeof(hdr), &h, &why)) {
        ESP_LOGE(TAG, "pxa: %s (no music flashed, or written by older firmware)", why);
        return {};
    }
    if (pxa::kHeaderSize + static_cast<uint64_t>(h.payload_len) > g_part->size) {
        ESP_LOGE(TAG, "pxa: payload larger than partition");
        return {};
    }
    std::vector<uint8_t> chunk(kCrcChunk);
    uint32_t crc = 0;
    for (uint32_t done = 0; done < h.payload_len;) {
        const size_t n = std::min<size_t>(kCrcChunk, h.payload_len - done);
        if (!read_flash(pxa::kHeaderSize + done, chunk.data(), n)) return {};
        crc = crc32::update(crc, chunk.data(), n);
        done += n;
    }
    if (crc != h.crc) {
        ESP_LOGE(TAG, "pxa: crc mismatch");
        return {};
    }
    std::vector<uint8_t> table(pxa::kEntrySize * h.file_count);
    if (!table.empty() && !read_flash(pxa::kHeaderSize, table.data(), table.size())) return {};
    std::vector<pxa::Entry> entries;
    if (!pxa::parse_table(table.data(), table.size(), h.file_count,
                          static_cast<uint32_t>(pxa::kHeaderSize + h.payload_len), &entries, &why)) {
        ESP_LOGE(TAG, "pxa: %s", why);
        return {};
    }
    return entries;
}

// Decodes one file from the partition and streams it to the speaker. Returns false (logged) on error.
bool play_file(const pxa::Entry &entry) {
    const std::string &name = entry.name;

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

    for (uint32_t pos = 0;;) {
        const size_t n = std::min<size_t>(in.size(), entry.length - pos);
        if (n > 0 && !read_flash(entry.offset + pos, in.data(), n)) return false;
        pos += static_cast<uint32_t>(n);
        esp_audio_simple_dec_raw_t raw = {};
        raw.buffer = in.data();
        raw.len = static_cast<uint32_t>(n);
        raw.eos = pos >= entry.length;
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
    const std::vector<pxa::Entry> playlist = load_playlist();
    if (playlist.empty()) {
        ESP_LOGW(TAG, "no tracks; add music with PixelBoard Studio");
        return;
    }
    ESP_LOGI(TAG, "playlist: %u file(s)", static_cast<unsigned>(playlist.size()));
    for (;;) {
        size_t played = 0;
        for (const pxa::Entry &entry : playlist) {
            ESP_LOGI(TAG, "playing %s", entry.name.c_str());
            if (play_file(entry)) ++played;
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
    g_part = esp_partition_find_first(ESP_PARTITION_TYPE_DATA,
                                      static_cast<esp_partition_subtype_t>(kAudioPartitionSubtype), kPartitionLabel);
    if (g_part == nullptr) {
        ESP_LOGE(TAG, "no \"audio\" partition (old partition table?)");
        esp_audio_simple_dec_unregister_default();
        esp_audio_dec_unregister_default();
        return false;
    }
    if (xTaskCreatePinnedToCore(task_main, "mp3", kTaskStack, nullptr, kTaskPriority, nullptr, kTaskCore) != pdPASS) {
        ESP_LOGE(TAG, "cannot create player task (out of memory)");
        esp_audio_simple_dec_unregister_default();
        esp_audio_dec_unregister_default();
        return false;
    }
    g_started = true;
    return true;
}

}  // namespace mp3_player
