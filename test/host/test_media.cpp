// Host-side unit tests for main/media (no ESP-IDF needed).
// Run: menu.sh "Host unit tests", or
//   g++ -std=c++17 -Wall -Wextra -Werror -I main test/host/test_media.cpp main/media.cpp -o /tmp/test_media && /tmp/test_media
#include <cstdio>

#include "media.h"

static int g_failures = 0;

#define CHECK(cond)                                                    \
    do {                                                               \
        if (!(cond)) {                                                 \
            std::printf("FAIL %s:%d: %s\n", __FILE__, __LINE__, #cond); \
            ++g_failures;                                              \
        }                                                              \
    } while (0)

static void test_pcm_format_supported() {
    CHECK(media::pcm_format_supported(44100, 2, 16));
    CHECK(media::pcm_format_supported(48000, 1, 16));
    CHECK(media::pcm_format_supported(8000, 1, 16));
    CHECK(media::pcm_format_supported(22050, 2, 16));
}

static void test_pcm_format_rejected() {
    CHECK(!media::pcm_format_supported(0, 2, 16));
    CHECK(!media::pcm_format_supported(96000, 2, 16));  // above codec limit
    CHECK(!media::pcm_format_supported(12345, 2, 16));  // not a standard rate
    CHECK(!media::pcm_format_supported(44100, 0, 16));
    CHECK(!media::pcm_format_supported(44100, 3, 16));
    CHECK(!media::pcm_format_supported(44100, 2, 24));
    CHECK(!media::pcm_format_supported(44100, 2, 8));
}

int main() {
    test_pcm_format_supported();
    test_pcm_format_rejected();
    if (g_failures == 0) {
        std::printf("test_media: all tests passed\n");
        return 0;
    }
    std::printf("test_media: %d failure(s)\n", g_failures);
    return 1;
}
