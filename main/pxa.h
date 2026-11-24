// PXA1 MP3 playlist stored in the `audio` partition (written by PixelBoard Studio /
// tools/pack_audio.py, see studio/pixelboard/pxa.py for the byte layout). Pure C++, host-testable.
#pragma once

#include <cstddef>
#include <cstdint>
#include <string>
#include <vector>

namespace pxa {

constexpr uint16_t kVersion = 1;
constexpr uint16_t kMaxFiles = 64;
constexpr size_t kHeaderSize = 32;
constexpr size_t kEntrySize = 72;
constexpr size_t kNameField = 64;
constexpr size_t kMaxNameLen = 60;  // bytes of UTF-8

struct Header {
    uint16_t file_count;
    uint32_t payload_len;  // table + data, i.e. everything after the header
    uint32_t crc;          // crc32 of the payload
};

struct Entry {
    std::string name;
    uint32_t offset;  // from the start of the partition
    uint32_t length;
};

// Validates magic, version, file count and that the table fits in the payload.
bool parse_header(const uint8_t *hdr, size_t len, Header *out, const char **err);

// Parses `count` table entries. Every file must lie after the table and end within `limit`
// bytes from the partition start (pass the smaller of partition size and header + payload).
bool parse_table(const uint8_t *table, size_t len, uint16_t count, uint32_t limit, std::vector<Entry> *out,
                 const char **err);

}  // namespace pxa
