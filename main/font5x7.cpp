#include "font5x7.h"

namespace font5x7 {
namespace {

struct Glyph {
    char ch;
    uint8_t cols[kGlyphWidth];
};

constexpr Glyph kGlyphs[] = {
    {' ', {0x00, 0x00, 0x00, 0x00, 0x00}},
    {'E', {0x7F, 0x49, 0x49, 0x49, 0x41}},
    {'H', {0x7F, 0x08, 0x08, 0x08, 0x7F}},
    {'L', {0x7F, 0x40, 0x40, 0x40, 0x40}},
    {'O', {0x3E, 0x41, 0x41, 0x41, 0x3E}},
};

}  // namespace

const uint8_t *glyph(char c) {
    for (const Glyph &g : kGlyphs) {
        if (g.ch == c) return g.cols;
    }
    return nullptr;
}

int text_width(const char *text) {
    if (text == nullptr || *text == '\0') return 0;
    int count = 0;
    for (const char *p = text; *p != '\0'; ++p) ++count;
    return count * kGlyphWidth + (count - 1) * kGlyphSpacing;
}

}  // namespace font5x7
