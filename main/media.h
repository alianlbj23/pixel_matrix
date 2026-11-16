// MP3 playlist and PCM format helpers. Pure C++, host-testable.
#pragma once

#include <cstddef>
#include <cstdint>
#include <string>
#include <vector>

namespace media {

// Longest file name accepted, in bytes (UTF-8). Must stay below CONFIG_SPIFFS_OBJ_NAME_LEN
// minus the leading "/" and the terminating NUL.
constexpr size_t kMaxNameLen = 60;

// Keeps plain "*.mp3" names (extension case-insensitive), drops hidden files, paths,
// over-long names and everything else, and sorts the result by byte order.
std::vector<std::string> make_playlist(const std::vector<std::string> &names);

// True if decoded PCM in this format can be sent to the ES8311 as-is.
bool pcm_format_supported(uint32_t sample_rate, int channels, int bits_per_sample);

}  // namespace media
