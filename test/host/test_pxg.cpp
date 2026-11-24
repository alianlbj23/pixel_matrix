// Host-side unit tests for main/pxg (no ESP-IDF needed). Run from the repo root:
//   g++ -std=c++17 -Wall -Wextra -Werror -I main test/host/test_pxg.cpp main/pxg.cpp main/crc32.cpp -o /tmp/t && /tmp/t
// test/host/fixtures/sample_v2.pxg is written by pxg.pack (3 panels (0,0,0) (64,0,0) (64,32,180), durations 100/200,
// frame 0 starts with pixel 1,2,3). test/host/fixtures/sample.pxg is the v1 fixture, written by studio/pixelboard/pxg.py (chain 2, brightness 90,
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

static std::vector<uint8_t> fixture_v2() {
    std::ifstream f("test/host/fixtures/sample_v2.pxg", std::ios::binary);
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

static void test_v1_fixture_still_parses() {
    const std::vector<uint8_t> data = fixture();
    CHECK(data.size() == 36904);
    pxg::View v{};
    const char *err = nullptr;
    CHECK(pxg::parse(data.data(), data.size(), &v, &err));
    CHECK(v.version == 1);
    CHECK(v.chain == 2);
    CHECK(v.brightness == 90);
    CHECK(!v.swap);
    CHECK(v.frame_count == 3);
    CHECK(duration(v, 0) == 100 && duration(v, 1) == 150 && duration(v, 2) == 200);
    CHECK(v.frame_bytes == 128u * 32u * 3u);
    CHECK(v.frames[0] == 255 && v.frames[1] == 0 && v.frames[2] == 0);
    CHECK(v.frames[v.frame_bytes] == 0 && v.frames[v.frame_bytes + 1] == 255);  // frame 1 green
}

static void test_parse_v2_fixture() {
    const std::vector<uint8_t> data = fixture_v2();
    // 32 header + 4 durations + 3*8 table + 2 frames of 3 panels
    CHECK(data.size() == 32u + 4u + 24u + 2u * 192u * 32u * 3u);
    pxg::View v{};
    const char *err = nullptr;
    CHECK(pxg::parse(data.data(), data.size(), &v, &err));
    CHECK(v.version == 2);
    CHECK(v.chain == 3);
    CHECK(v.brightness == 90);
    CHECK(!v.swap);
    CHECK(v.frame_count == 2);
    CHECK(duration(v, 0) == 100 && duration(v, 1) == 200);
    CHECK(v.frame_bytes == 192u * 32u * 3u);
    CHECK(v.frames[0] == 1 && v.frames[1] == 2 && v.frames[2] == 3);
    CHECK(v.frames[v.frame_bytes] == 9);
}

static void test_rejects_v2_chain_9() {
    std::vector<uint8_t> d = fixture_v2();
    d[10] = 9;
    CHECK(!parses(d));
    d = fixture_v2();
    d[10] = 0;
    CHECK(!parses(d));
}

static void test_rejects_v2_inconsistent_length() {
    std::vector<uint8_t> d = fixture_v2();
    d[10] = 2;  // payload_len no longer matches chain 2
    CHECK(!parses(d));
    d = fixture_v2();
    d.pop_back();
    CHECK(!parses(d));
}

static void test_rejects_v3() {
    std::vector<uint8_t> d = fixture_v2();
    d[4] = 3;
    CHECK(!parses(d));
    d = fixture();
    d[4] = 3;
    CHECK(!parses(d));
}

static void test_rejects_bad_magic() {
    std::vector<uint8_t> d = fixture();
    d[0] = 'X';
    CHECK(!parses(d));
}

static void test_v1_fixture_as_v2_rejected() {
    std::vector<uint8_t> d = fixture();
    d[4] = 2;  // v1 payload has no arrangement table, so the length check must fail
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
    test_v1_fixture_still_parses();
    test_parse_v2_fixture();
    test_rejects_bad_magic();
    test_v1_fixture_as_v2_rejected();
    test_rejects_v2_chain_9();
    test_rejects_v2_inconsistent_length();
    test_rejects_v3();
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
