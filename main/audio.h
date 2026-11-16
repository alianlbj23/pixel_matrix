// Speaker output through the onboard ES8311 codec and power amp.
#pragma once

#include <cstddef>
#include <cstdint>

namespace audio {

// Sets up I2C, I2S, the ES8311 codec and the power amp. Logs and returns false on
// failure (everything created so far is released). Calling again after success is a no-op.
bool init();

// The functions below are not thread-safe: use them from one task at a time.

// Plays a sine tone and blocks until it has been queued to I2S. Returns false if
// audio is not initialised, the arguments are invalid, or an I2S write times out.
bool play_tone(float freq_hz, uint32_t duration_ms);

// Switches the codec/I2S to this 16-bit PCM format (no-op if already active).
// Returns false (logged) if not initialised or the codec cannot be reopened.
bool set_format(uint32_t sample_rate, int channels);

// Queues interleaved 16-bit PCM in the current format; blocks until queued.
// Returns false (logged) if not initialised or an I2S write times out.
bool write_pcm(const void *data, size_t bytes);

}  // namespace audio
