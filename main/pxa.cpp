#include "pxa.h"

#include <cstring>

namespace pxa {
namespace {

uint16_t rd16(const uint8_t *p) { return static_cast<uint16_t>(p[0] | (p[1] << 8)); }
uint32_t rd32(const uint8_t *p) {
    return static_cast<uint32_t>(p[0]) | (static_cast<uint32_t>(p[1]) << 8) |
           (static_cast<uint32_t>(p[2]) << 16) | (static_cast<uint32_t>(p[3]) << 24);
}

bool fail(const char **err, const char *why) {
    if (err != nullptr) *err = why;
    return false;
}

}  // namespace

bool parse_header(const uint8_t *hdr, size_t len, Header *out, const char **err) {
    if (hdr == nullptr || out == nullptr) return fail(err, "null argument");
    if (len < kHeaderSize) return fail(err, "shorter than header");
    if (std::memcmp(hdr, "PXA1", 4) != 0) return fail(err, "bad magic");
    if (rd16(hdr + 4) != kVersion) return fail(err, "unsupported version");
    const uint16_t count = rd16(hdr + 6);
    const uint32_t payload_len = rd32(hdr + 8);
    if (count > kMaxFiles) return fail(err, "too many files");
    if (payload_len < kEntrySize * count) return fail(err, "payload shorter than file table");
    out->file_count = count;
    out->payload_len = payload_len;
    out->crc = rd32(hdr + 12);
    return true;
}

bool parse_table(const uint8_t *table, size_t len, uint16_t count, uint32_t limit, std::vector<Entry> *out,
                 const char **err) {
    if (out == nullptr || (table == nullptr && count > 0)) return fail(err, "null argument");
    if (count > kMaxFiles) return fail(err, "too many files");
    if (len < kEntrySize * count) return fail(err, "table truncated");
    const uint64_t table_end = kHeaderSize + kEntrySize * count;
    std::vector<Entry> entries;
    entries.reserve(count);
    for (uint16_t i = 0; i < count; ++i) {
        const uint8_t *e = table + kEntrySize * i;
        const void *nul = std::memchr(e, 0, kMaxNameLen + 1);
        if (nul == nullptr) return fail(err, "name not terminated");
        const size_t name_len = static_cast<const uint8_t *>(nul) - e;
        if (name_len == 0) return fail(err, "empty name");
        const uint32_t offset = rd32(e + kNameField);
        const uint32_t length = rd32(e + kNameField + 4);
        if (offset < table_end) return fail(err, "file overlaps header or table");
        if (static_cast<uint64_t>(offset) + length > limit) return fail(err, "file outside partition");
        entries.push_back(Entry{std::string(reinterpret_cast<const char *>(e), name_len), offset, length});
    }
    *out = std::move(entries);
    return true;
}

}  // namespace pxa
