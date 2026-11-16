// Sine tone generator producing interleaved 16-bit stereo PCM. Pure C++, host-testable.
#pragma once

#include <cstddef>
#include <cstdint>

namespace tone {

constexpr int kChannels = 2;

class SineTone {
public:
    // Prepares a tone of total_frames frames. fade_frames of linear fade-in and fade-out
    // avoid clicks. Returns false (and leaves the tone done) on invalid arguments.
    bool start(uint32_t sample_rate, float freq_hz, float amplitude, uint32_t total_frames,
               uint32_t fade_frames);

    // Writes up to max_frames stereo frames (2 * max_frames samples) into buf.
    // Returns the number of frames written; 0 once the tone is finished.
    size_t fill(int16_t *buf, size_t max_frames);

    bool done() const { return pos_ >= total_; }

private:
    double phase_step_ = 0.0;  // radians per frame
    float amplitude_ = 0.0f;
    uint32_t total_ = 0;
    uint32_t fade_ = 0;
    uint32_t pos_ = 0;
};

}  // namespace tone
