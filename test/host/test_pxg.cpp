// Host-side unit tests for main/pxg (no ESP-IDF needed). Run from the repo root:
//   g++ -std=c++17 -Wall -Wextra -Werror -I main test/host/test_pxg.cpp main/pxg.cpp main/crc32.cpp -o /tmp/t && /tmp/t
// test/host/fixtures/sample.pxg is written by studio/pixelboard/pxg.py (chain 2, brightness 90,
// durations 100/150/200, frame 0 starts with a red pixel) and keeps both sides in sync.
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <fstream>
#include <iterator>
#include <vector>

#include "pxg.h"

static int g_failures = 0;
#define CHECK(cond)                                                    \
    do {                                                               \
        if (!(cond)) {                                                 \
            std::printf("FAIL %s:%d: %s\n", __FILE__, __LINE__, #cond); \
            ++g_failures;                                              \
        }                                                              \
    } while (0)

static std::vector<uint8_t> fixture() {
    std::ifstream f("test/host/fixtures/sample.pxg", std::ios::binary);
    return std::vector<uint8_t>(std::istreambuf_iterator<char>(f), {});
}

static uint16_t duration(const pxg::View &v, int i) {
    uint16_t d;
    std::memcpy(&d, reinterpret_cast<const uint8_t *>(v.durations) + 2 * i, 2);
    return d;
}

static bool parses(const std::vector<uint8_t> &data) {
    pxg::View v{};
    const char *err = nullptr;
    const bool ok = pxg::parse(data.data(), data.size(), &v, &err);
    CHECK(ok || err != nullptr);  // failures always say why
    return ok;
}

static void test_parse_fixture() {
    const std::vector<uint8_t> data = fixture();
    CHECK(data.size() == 36904);
    pxg::View v{};
    const char *err = nullptr;
    CHECK(pxg::parse(data.data(), data.size(), &v, &err));
    CHECK(v.chain == 2);
    CHECK(v.brightness == 90);
    CHECK(!v.swap);
    CHECK(v.frame_count == 3);
    CHECK(duration(v, 0) == 100 && duration(v, 1) == 150 && duration(v, 2) == 200);
    CHECK(v.frame_bytes == 128u * 32u * 3u);
    CHECK(v.frames[0] == 255 && v.frames[1] == 0 && v.frames[2] == 0);
    CHECK(v.frames[v.frame_bytes] == 0 && v.frames[v.frame_bytes + 1] == 255);  // frame 1 green
}

static void test_rejects_bad_magic() {
    std::vector<uint8_t> d = fixture();
    d[0] = 'X';
    CHECK(!parses(d));
}

static void test_rejects_version_2() {
    std::vector<uint8_t> d = fixture();
    d[4] = 2;
    CHECK(!parses(d));
}

static void test_rejects_crc_mismatch() {
    std::vector<uint8_t> d = fixture();
    d.back() ^= 0xFF;
    CHECK(!parses(d));
}

static void test_rejects_truncated() {
    std::vector<uint8_t> d = fixture();
    d.pop_back();
    CHECK(!parses(d));
    d.resize(20);
    CHECK(!parses(d));
    CHECK(!pxg::parse(nullptr, 0, nullptr, nullptr));
}

static void test_rejects_chain_3() {
    std::vector<uint8_t> d = fixture();
    d[10] = 3;
    CHECK(!parses(d));
}

static void test_rejects_erased_flash() {
    std::vector<uint8_t> d(4096, 0xFF);
    CHECK(!parses(d));
}

static void test_panel_x() {
    CHECK(pxg::panel_x(0, 2, true) == 64);
    CHECK(pxg::panel_x(127, 2, true) == 63);
    CHECK(pxg::panel_x(5, 2, false) == 5);
    CHECK(pxg::panel_x(5, 1, true) == 5);
}

int main() {
    test_parse_fixture();
    test_rejects_bad_magic();
    test_rejects_version_2();
    test_rejects_crc_mismatch();
    test_rejects_truncated();
    test_rejects_chain_3();
    test_rejects_erased_flash();
    test_panel_x();
    if (g_failures == 0) {
        std::printf("test_pxg: all tests passed\n");
        return 0;
    }
    std::printf("test_pxg: %d failure(s)\n", g_failures);
    return 1;
}
