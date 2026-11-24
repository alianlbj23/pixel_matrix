#include "identify.h"

#include "board_config.h"
#include "font5x7.h"

namespace identify {
namespace {

constexpr int kW = kPanelWidth;
constexpr int kH = kPanelHeight;
constexpr int kScale = 3;
constexpr int kNumberX = 6;  // panel-local top-left of the 3x panel number
constexpr int kNumberY = 5;
constexpr int kArrowX = 41;  // 3x '^' (15 px wide) centred in lx 40..56; its tip is at the smallest ly
constexpr int kArrowY = 5;

struct Color {
    uint8_t r, g, b;
};
constexpr Color kBackground{0, 0, 0};
constexpr Color kBorder{0, 80, 255};
constexpr Color kNumber{255, 255, 255};
constexpr Color kArrow{255, 200, 0};

// Draws one font5x7 glyph kScale times larger with its top-left at chain coordinates (x, y).
void draw_big_char(const SetPixel &set_pixel, char ch, int x, int y, Color c) {
    const char text[2] = {ch, '\0'};
    font5x7::draw_text(text, 0, 0, [&](int gx, int gy) {
        for (int dy = 0; dy < kScale; ++dy) {
            for (int dx = 0; dx < kScale; ++dx) set_pixel(x + gx * kScale + dx, y + gy * kScale + dy, c.r, c.g, c.b);
        }
    });
}

}  // namespace

void draw(int n, const SetPixel &set_pixel) {
    if (n < 1 || n > kMaxPanels) return;
    for (int i = 0; i < n; ++i) {
        const int ox = kW * i;
        for (int ly = 0; ly < kH; ++ly) {
            for (int lx = 0; lx < kW; ++lx) {
                const bool edge = lx == 0 || lx == kW - 1 || ly == 0 || ly == kH - 1;
                const Color c = edge ? kBorder : kBackground;
                set_pixel(ox + lx, ly, c.r, c.g, c.b);
            }
        }
        draw_big_char(set_pixel, static_cast<char>('1' + i), ox + kNumberX, kNumberY, kNumber);
        draw_big_char(set_pixel, '^', ox + kArrowX, kArrowY, kArrow);
    }
}

}  // namespace identify
