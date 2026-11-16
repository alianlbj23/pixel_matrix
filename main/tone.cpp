#include "tone.h"

#include <cmath>

namespace tone {

namespace {
constexpr double kTwoPi = 6.283185307179586;
constexpr float kFullScale = 32767.0f;
}  // namespace

bool SineTone::start(uint32_t sample_rate, float freq_hz, float amplitude, uint32_t total_frames,
                     uint32_t fade_frames) {
    total_ = 0;
    pos_ = 0;
    const bool valid = sample_rate > 0 && freq_hz > 0.0f &&
                       freq_hz < static_cast<float>(sample_rate) / 2.0f && amplitude >= 0.0f &&
                       amplitude <= 1.0f && total_frames > 0 &&
                       static_cast<uint64_t>(fade_frames) * 2 <= total_frames;
    if (!valid) return false;
    phase_step_ = kTwoPi * static_cast<double>(freq_hz) / static_cast<double>(sample_rate);
    amplitude_ = amplitude;
    total_ = total_frames;
    fade_ = fade_frames;
    return true;
}

size_t SineTone::fill(int16_t *buf, size_t max_frames) {
    if (buf == nullptr || done()) return 0;
    size_t n = 0;
    for (; n < max_frames && pos_ < total_; ++n, ++pos_) {
        float gain = 1.0f;
        if (fade_ > 0) {
            const uint32_t from_end = total_ - 1 - pos_;
            const uint32_t edge = pos_ < from_end ? pos_ : from_end;
            if (edge < fade_) gain = static_cast<float>(edge) / static_cast<float>(fade_);
        }
        // Phase from the frame index (not accumulated) so long tones do not drift.
        const double phase = std::fmod(phase_step_ * static_cast<double>(pos_), kTwoPi);
        // Truncate toward zero so |sample| never exceeds amplitude * full scale.
        const auto sample = static_cast<int16_t>(amplitude_ * gain * kFullScale *
                                                 static_cast<float>(std::sin(phase)));
        buf[n * kChannels] = sample;
        buf[n * kChannels + 1] = sample;
    }
    return n;
}

}  // namespace tone
