// Host-side unit tests for main/tone (no ESP-IDF needed).
// Run: menu.sh "Host unit tests", or
//   g++ -std=c++17 -Wall -Wextra -Werror -I main test/host/test_tone.cpp main/tone.cpp -o /tmp/test_tone && /tmp/test_tone
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <vector>

#include "tone.h"

static int g_failures = 0;

#define CHECK(cond)                                                    \
    do {                                                               \
        if (!(cond)) {                                                 \
            std::printf("FAIL %s:%d: %s\n", __FILE__, __LINE__, #cond); \
            ++g_failures;                                              \
        }                                                              \
    } while (0)

// Pulls the whole tone out in chunks of `chunk` frames; returns interleaved samples.
static std::vector<int16_t> render(tone::SineTone &t, size_t chunk) {
    std::vector<int16_t> out;
    std::vector<int16_t> buf(chunk * tone::kChannels);
    for (;;) {
        const size_t n = t.fill(buf.data(), chunk);
        if (n == 0) break;
        out.insert(out.end(), buf.begin(), buf.begin() + static_cast<long>(n * tone::kChannels));
    }
    return out;
}

static void test_rejects_invalid_args() {
    tone::SineTone t;
    CHECK(!t.start(0, 440.0f, 0.5f, 100, 0));          // sample rate 0
    CHECK(!t.start(16000, 0.0f, 0.5f, 100, 0));        // freq 0
    CHECK(!t.start(16000, -5.0f, 0.5f, 100, 0));       // negative freq
    CHECK(!t.start(16000, 8000.0f, 0.5f, 100, 0));     // at Nyquist
    CHECK(!t.start(16000, 440.0f, 1.5f, 100, 0));      // amplitude > 1
    CHECK(!t.start(16000, 440.0f, -0.1f, 100, 0));     // amplitude < 0
    CHECK(!t.start(16000, 440.0f, 0.5f, 0, 0));        // zero length
    CHECK(!t.start(16000, 440.0f, 0.5f, 100, 51));     // fades longer than tone
    CHECK(t.done());
}

static void test_fill_before_start_or_null_buffer_returns_zero() {
    tone::SineTone t;
    int16_t buf[8] = {};
    CHECK(t.fill(buf, 4) == 0);
    CHECK(t.start(16000, 440.0f, 0.5f, 100, 0));
    CHECK(t.fill(nullptr, 4) == 0);
    CHECK(t.fill(buf, 0) == 0);
}

static void test_exact_length_across_chunks() {
    tone::SineTone t;
    CHECK(t.start(16000, 440.0f, 0.5f, 1000, 10));
    CHECK(!t.done());
    const std::vector<int16_t> s = render(t, 64);  // 1000 is not a multiple of 64
    CHECK(s.size() == 1000u * tone::kChannels);
    CHECK(t.done());
}

static void test_stereo_channels_equal() {
    tone::SineTone t;
    CHECK(t.start(16000, 1000.0f, 0.8f, 500, 0));
    const std::vector<int16_t> s = render(t, 100);
    for (size_t i = 0; i + 1 < s.size(); i += 2) CHECK(s[i] == s[i + 1]);
}

static void test_peak_matches_amplitude() {
    tone::SineTone t;
    const float amp = 0.5f;
    CHECK(t.start(16000, 440.0f, amp, 16000, 0));
    const std::vector<int16_t> s = render(t, 256);
    int peak = 0;
    for (int16_t v : s) peak = std::abs(v) > peak ? std::abs(v) : peak;
    const int expected = static_cast<int>(amp * 32767.0f);
    CHECK(peak <= expected);
    CHECK(peak >= expected - expected / 100);  // within 1%
}

static void test_frequency_by_zero_crossings() {
    tone::SineTone t;
    CHECK(t.start(16000, 440.0f, 0.5f, 16000, 0));  // exactly 1 s
    const std::vector<int16_t> s = render(t, 333);
    int rising = 0;
    for (size_t i = 2; i < s.size(); i += 2) {
        if (s[i - 2] < 0 && s[i] >= 0) ++rising;
    }
    CHECK(rising >= 439 && rising <= 441);
}

static void test_fade_starts_and_ends_near_silence() {
    tone::SineTone t;
    CHECK(t.start(16000, 440.0f, 1.0f, 4000, 160));
    const std::vector<int16_t> s = render(t, 128);
    CHECK(std::abs(s.front()) < 100);
    CHECK(std::abs(s[s.size() - 1]) < 400);
    // Middle of the tone reaches near full scale.
    int mid_peak = 0;
    for (size_t i = s.size() / 2 - 200; i < s.size() / 2 + 200; ++i) {
        mid_peak = std::abs(s[i]) > mid_peak ? std::abs(s[i]) : mid_peak;
    }
    CHECK(mid_peak > 32000);
}

static void test_restart_resets_state() {
    tone::SineTone t;
    CHECK(t.start(16000, 440.0f, 0.5f, 10, 0));
    render(t, 4);
    CHECK(t.done());
    CHECK(t.start(16000, 880.0f, 0.5f, 20, 0));
    CHECK(render(t, 7).size() == 20u * tone::kChannels);
}

int main() {
    test_rejects_invalid_args();
    test_fill_before_start_or_null_buffer_returns_zero();
    test_exact_length_across_chunks();
    test_stereo_channels_equal();
    test_peak_matches_amplitude();
    test_frequency_by_zero_crossings();
    test_fade_starts_and_ends_near_silence();
    test_restart_resets_state();
    if (g_failures == 0) {
        std::printf("test_tone: all tests passed\n");
        return 0;
    }
    std::printf("test_tone: %d failure(s)\n", g_failures);
    return 1;
}
