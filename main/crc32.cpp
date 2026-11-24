#include "crc32.h"

#include <array>

namespace crc32 {
namespace {

constexpr std::array<uint32_t, 256> make_table() {
    std::array<uint32_t, 256> t{};
    for (uint32_t i = 0; i < 256; ++i) {
        uint32_t c = i;
        for (int k = 0; k < 8; ++k) c = (c & 1u) ? 0xEDB88320u ^ (c >> 1) : c >> 1;
        t[i] = c;
    }
    return t;
}

constexpr std::array<uint32_t, 256> kTable = make_table();

}  // namespace

uint32_t update(uint32_t crc, const uint8_t *data, size_t len) {
    crc = ~crc;
    for (size_t i = 0; i < len; ++i) crc = kTable[(crc ^ data[i]) & 0xFFu] ^ (crc >> 8);
    return ~crc;
}

}  // namespace crc32
