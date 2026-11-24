// Host-side unit tests for main/pxa (no ESP-IDF needed). Run from the repo root:
//   g++ -std=c++17 -Wall -Wextra -Werror -I main test/host/test_pxa.cpp main/pxa.cpp main/crc32.cpp -o /tmp/t && /tmp/t
// test/host/fixtures/sample.pxa is written by studio/pixelboard/pxa.py: tracks "a.mp3"
// (3 stereo frames, 1251 bytes) and "歌.mp3" (3 mono frames, 1251 bytes).
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <fstream>
#include <iterator>
#include <string>
#include <vector>

#include "crc32.h"
#include "pxa.h"

static int g_failures = 0;
#define CHECK(cond)                                                    \
    do {                                                               \
        if (!(cond)) {                                                 \
            std::printf("FAIL %s:%d: %s\n", __FILE__, __LINE__, #cond); \
            ++g_failures;                                              \
        }                                                              \
    } while (0)

static std::vector<uint8_t> fixture() {
    std::ifstream f("test/host/fixtures/sample.pxa", std::ios::binary);
    return std::vector<uint8_t>(std::istreambuf_iterator<char>(f), {});
}

static void put32(std::vector<uint8_t> &d, size_t at, uint32_t v) {
    for (int i = 0; i < 4; ++i) d[at + i] = static_cast<uint8_t>(v >> (8 * i));
}

// Parses header + table of an in-memory image, as the player does with flash reads.
static bool parse_all(const std::vector<uint8_t> &d, std::vector<pxa::Entry> *entries) {
    pxa::Header h{};
    const char *err = nullptr;
    if (!pxa::parse_header(d.data(), d.size(), &h, &err)) {
        CHECK(err != nullptr);
        return false;
    }
    const size_t table = pxa::kEntrySize * h.file_count;
    if (d.size() < pxa::kHeaderSize + table) return false;
    if (!pxa::parse_table(d.data() + pxa::kHeaderSize, table, h.file_count, static_cast<uint32_t>(d.size()),
                          entries, &err)) {
        CHECK(err != nullptr);
        return false;
    }
    return true;
}

static void test_parse_fixture() {
    const std::vector<uint8_t> d = fixture();
    pxa::Header h{};
    const char *err = nullptr;
    CHECK(pxa::parse_header(d.data(), d.size(), &h, &err));
    CHECK(h.file_count == 2);
    CHECK(h.payload_len == d.size() - pxa::kHeaderSize);
    CHECK(crc32::update(0, d.data() + pxa::kHeaderSize, h.payload_len) == h.crc);
    std::vector<pxa::Entry> e;
    CHECK(parse_all(d, &e));
    CHECK(e.size() == 2);
    if (e.size() == 2) {
        CHECK(e[0].name == "a.mp3");
        CHECK(e[1].name == "\xe6\xad\x8c.mp3");  // "歌.mp3"
        CHECK(e[0].offset % 4 == 0 && e[1].offset % 4 == 0);
        CHECK(e[0].length == 1251 && e[1].length == 1251);
        CHECK(d[e[0].offset] == 0xFF && d[e[0].offset + 1] == 0xFB);  // MP3 sync word
    }
}

static void test_rejects_bad_magic_and_old_spiffs() {
    std::vector<uint8_t> d = fixture();
    d[0] = 'X';
    std::vector<pxa::Entry> e;
    CHECK(!parse_all(d, &e));
    // Previous firmware stored a SPIFFS image here; its first bytes are not "PXA1".
    std::vector<uint8_t> spiffs(4096, 0xFF);
    spiffs[0] = 0x01;
    CHECK(!parse_all(spiffs, &e));
}

static void test_rejects_version_2() {
    std::vector<uint8_t> d = fixture();
    d[4] = 2;
    std::vector<pxa::Entry> e;
    CHECK(!parse_all(d, &e));
}

static void test_rejects_65_files() {
    std::vector<uint8_t> d = fixture();
    d[6] = 65;
    pxa::Header h{};
    CHECK(!pxa::parse_header(d.data(), d.size(), &h, nullptr));
}

static void test_rejects_entry_past_partition() {
    std::vector<uint8_t> d = fixture();
    put32(d, pxa::kHeaderSize + pxa::kEntrySize + 68, 0x01000000);  // entry 1 length 16 MB
    std::vector<pxa::Entry> e;
    CHECK(!parse_all(d, &e));
}

static void test_rejects_entry_overlapping_table() {
    std::vector<uint8_t> d = fixture();
    put32(d, pxa::kHeaderSize + 64, pxa::kHeaderSize);  // entry 0 points into the table
    std::vector<pxa::Entry> e;
    CHECK(!parse_all(d, &e));
}

static void test_rejects_unterminated_name() {
    std::vector<uint8_t> d = fixture();
    std::memset(&d[pxa::kHeaderSize], 'a', 64);  // name fills the whole field, no NUL
    std::vector<pxa::Entry> e;
    CHECK(!parse_all(d, &e));
    std::memset(&d[pxa::kHeaderSize], 0, 64);   // empty name
    CHECK(!parse_all(d, &e));
}

static void test_empty_image() {
    std::vector<uint8_t> d(pxa::kHeaderSize, 0);
    std::memcpy(d.data(), "PXA1", 4);
    d[4] = 1;
    put32(d, 12, crc32::update(0, nullptr, 0));
    pxa::Header h{};
    CHECK(pxa::parse_header(d.data(), d.size(), &h, nullptr));
    CHECK(h.file_count == 0 && h.payload_len == 0);
}

int main() {
    test_parse_fixture();
    test_rejects_bad_magic_and_old_spiffs();
    test_rejects_version_2();
    test_rejects_65_files();
    test_rejects_entry_past_partition();
    test_rejects_entry_overlapping_table();
    test_rejects_unterminated_name();
    test_empty_image();
    if (g_failures == 0) {
        std::printf("test_pxa: all tests passed\n");
        return 0;
    }
    std::printf("test_pxa: %d failure(s)\n", g_failures);
    return 1;
}
