// Minimal 5x7 bitmap font, only the characters the firmware draws ("HELLO", "NO GIF", digits and
// the identify arrow '^').
#pragma once

#include <cstdint>

namespace font5x7 {

constexpr int kGlyphWidth = 5;
constexpr int kGlyphHeight = 7;
constexpr int kGlyphSpacing = 1;

// Returns 5 column bytes (bit 0 = top row), or nullptr if c is not in the font.
const uint8_t *glyph(char c);

// Width in pixels of text; unsupported characters still take one glyph cell.
int text_width(const char *text);

// Calls set_pixel(px, py) for every lit pixel, with the text's top-left at (x, y).
// Unsupported characters are left blank. Returns how many were unsupported.
template <typename SetPixel>
int draw_text(const char *text, int x, int y, SetPixel set_pixel) {
    if (text == nullptr) return 0;
    int unsupported = 0;
    for (const char *p = text; *p != '\0'; ++p) {
        const uint8_t *cols = glyph(*p);
        if (cols == nullptr) {
            ++unsupported;
        } else {
            for (int col = 0; col < kGlyphWidth; ++col) {
                for (int row = 0; row < kGlyphHeight; ++row) {
                    if (cols[col] & (1u << row)) set_pixel(x + col, y + row);
                }
            }
        }
        x += kGlyphWidth + kGlyphSpacing;
    }
    return unsupported;
}

}  // namespace font5x7
