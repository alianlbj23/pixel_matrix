// PXG1 animation format stored in the `gif` partition (written by PixelBoard Studio /
// tools/pack_gif.py, see studio/pixelboard/pxg.py for the byte layout). Pure C++, host-testable.
#pragma once

#include <cstddef>
#include <cstdint>

namespace pxg {

constexpr uint16_t kVersion = 1;
constexpr uint16_t kPanelW = 64;
constexpr uint16_t kPanelH = 32;
constexpr uint16_t kMaxFrames = 64;
constexpr uint8_t kMinBrightness = 10;
constexpr uint8_t kMaxBrightness = 128;
constexpr size_t kHeaderSize = 32;

// Points into the parsed buffer; valid as long as that buffer is.
struct View {
    uint8_t chain;              // 1 or 2 panels
    uint8_t brightness;         // 10..128
    bool swap;                  // panels cabled right-to-left
    uint16_t frame_count;       // 1..64
    const uint16_t *durations;  // may be unaligned: read with duration_ms()
    const uint8_t *frames;      // frame_count x frame_bytes, RGB888 row-major
    size_t frame_bytes;         // 64 * chain * 32 * 3
};

// Validates header, sizes and CRC. On failure returns false and sets *err (if err != nullptr).
bool parse(const uint8_t *data, size_t len, View *out, const char **err);

uint16_t duration_ms(const View &view, uint16_t frame);

// Physical panel column for data column x: with swap on 2 panels the halves are exchanged.
int panel_x(int x, uint8_t chain, bool swap);

}  // namespace pxg
