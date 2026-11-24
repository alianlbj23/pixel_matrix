// CRC-32/ISO-HDLC (same as zlib.crc32 / Python zlib.crc32). Pure C++, host-testable.
#pragma once

#include <cstddef>
#include <cstdint>

namespace crc32 {

// Continues a CRC: start with crc = 0; update(update(0, a), b) == update(0, a + b).
uint32_t update(uint32_t crc, const uint8_t *data, size_t len);

}  // namespace crc32
