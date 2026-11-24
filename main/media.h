// PCM format helper for decoded MP3 audio. Pure C++, host-testable.
#pragma once

#include <cstddef>
#include <cstdint>

namespace media {

// True if decoded PCM in this format can be sent to the ES8311 as-is.
bool pcm_format_supported(uint32_t sample_rate, int channels, int bits_per_sample);

}  // namespace media
