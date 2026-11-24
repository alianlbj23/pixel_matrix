#include "pxg.h"

#include <cstring>

#include "crc32.h"

namespace pxg {
namespace {

uint16_t rd16(const uint8_t *p) { return static_cast<uint16_t>(p[0] | (p[1] << 8)); }
uint32_t rd32(const uint8_t *p) {
    return static_cast<uint32_t>(p[0]) | (static_cast<uint32_t>(p[1]) << 8) |
           (static_cast<uint32_t>(p[2]) << 16) | (static_cast<uint32_t>(p[3]) << 24);
}
size_t pad4(size_t n) { return (n + 3) & ~static_cast<size_t>(3); }

bool fail(const char **err, const char *why) {
    if (err != nullptr) *err = why;
    return false;
}

}  // namespace

bool parse(const uint8_t *data, size_t len, View *out, const char **err) {
    if (data == nullptr || out == nullptr) return fail(err, "null argument");
    if (len < kHeaderSize) return fail(err, "shorter than header");
    if (std::memcmp(data, "PXG1", 4) != 0) return fail(err, "bad magic");
    if (rd16(data + 4) != kVersion) return fail(err, "unsupported version");
    if (rd16(data + 6) != kPanelW || rd16(data + 8) != kPanelH) return fail(err, "unsupported panel size");
    const uint8_t chain = data[10];
    const uint8_t brightness = data[11];
    const uint16_t count = rd16(data + 16);
    if (chain != 1 && chain != 2) return fail(err, "bad chain");
    if (brightness < kMinBrightness || brightness > kMaxBrightness) return fail(err, "bad brightness");
    if (count == 0 || count > kMaxFrames) return fail(err, "bad frame count");
    const size_t frame_bytes = static_cast<size_t>(kPanelW) * chain * kPanelH * 3;
    const size_t payload_len = rd32(data + 20);
    if (payload_len != pad4(2u * count) + count * frame_bytes) return fail(err, "inconsistent payload length");
    if (len - kHeaderSize < payload_len) return fail(err, "truncated");
    const uint8_t *payload = data + kHeaderSize;
    if (crc32::update(0, payload, payload_len) != rd32(data + 24)) return fail(err, "crc mismatch");
    out->chain = chain;
    out->brightness = brightness;
    out->swap = (data[12] & 0x01u) != 0;
    out->frame_count = count;
    out->durations = reinterpret_cast<const uint16_t *>(payload);
    out->frames = payload + pad4(2u * count);
    out->frame_bytes = frame_bytes;
    return true;
}

uint16_t duration_ms(const View &view, uint16_t frame) {
    return rd16(reinterpret_cast<const uint8_t *>(view.durations) + 2u * frame);
}

int panel_x(int x, uint8_t chain, bool swap) {
    if (!swap || chain != 2) return x;
    return x < kPanelW ? x + kPanelW : x - kPanelW;
}

}  // namespace pxg
