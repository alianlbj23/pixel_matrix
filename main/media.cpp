#include "media.h"

#include <algorithm>
#include <iterator>

namespace media {

bool pcm_format_supported(uint32_t sample_rate, int channels, int bits_per_sample) {
    static constexpr uint32_t kRates[] = {8000, 11025, 12000, 16000, 22050, 24000, 32000, 44100, 48000};
    const bool rate_ok = std::find(std::begin(kRates), std::end(kRates), sample_rate) != std::end(kRates);
    return rate_ok && (channels == 1 || channels == 2) && bits_per_sample == 16;
}

}  // namespace media
