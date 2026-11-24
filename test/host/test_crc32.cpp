// Host-side unit tests for main/crc32 (no ESP-IDF needed).
#include <cstdint>
#include <cstdio>

#include "crc32.h"

static int g_failures = 0;
#define CHECK(cond)                                                    \
    do {                                                               \
        if (!(cond)) {                                                 \
            std::printf("FAIL %s:%d: %s\n", __FILE__, __LINE__, #cond); \
            ++g_failures;                                              \
        }                                                              \
    } while (0)

int main() {
    const uint8_t check[] = {'1', '2', '3', '4', '5', '6', '7', '8', '9'};
    CHECK(crc32::update(0, check, sizeof(check)) == 0xCBF43926u);    // CRC-32/ISO-HDLC check value
    CHECK(crc32::update(0, nullptr, 0) == 0u);
    // Streaming in pieces gives the same result as one call.
    uint32_t c = crc32::update(0, check, 4);
    c = crc32::update(c, check + 4, 5);
    CHECK(c == 0xCBF43926u);
    if (g_failures == 0) {
        std::printf("test_crc32: all tests passed\n");
        return 0;
    }
    std::printf("test_crc32: %d failure(s)\n", g_failures);
    return 1;
}
