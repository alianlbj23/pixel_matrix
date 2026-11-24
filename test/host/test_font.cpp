// Host-side unit tests for main/font5x7 (no ESP-IDF needed).
// Run: menu.sh option 6, or
//   g++ -std=c++17 -Wall -Wextra -Werror -I main test/host/test_font.cpp main/font5x7.cpp -o /tmp/test_font && /tmp/test_font
#include <cstdio>
#include <set>
#include <utility>

#include "font5x7.h"

static int g_failures = 0;

#define CHECK(cond)                                                    \
    do {                                                               \
        if (!(cond)) {                                                 \
            std::printf("FAIL %s:%d: %s\n", __FILE__, __LINE__, #cond); \
            ++g_failures;                                              \
        }                                                              \
    } while (0)

using Pixels = std::set<std::pair<int, int>>;

static Pixels render(const char *text, int x, int y, int *unsupported) {
    Pixels px;
    *unsupported = font5x7::draw_text(text, x, y, [&px](int px_x, int px_y) { px.insert({px_x, px_y}); });
    return px;
}

static void test_glyph_lookup() {
    const uint8_t *h = font5x7::glyph('H');
    CHECK(h != nullptr);
    if (h != nullptr) {
        const uint8_t expected[5] = {0x7F, 0x08, 0x08, 0x08, 0x7F};
        for (int i = 0; i < 5; ++i) CHECK(h[i] == expected[i]);
    }
    CHECK(font5x7::glyph('E') != nullptr);
    CHECK(font5x7::glyph('L') != nullptr);
    CHECK(font5x7::glyph('O') != nullptr);
    CHECK(font5x7::glyph(' ') != nullptr);
}

static void test_glyph_unsupported() {
    CHECK(font5x7::glyph('h') == nullptr);
    CHECK(font5x7::glyph('#') == nullptr);
    CHECK(font5x7::glyph('\0') == nullptr);
}

static void test_text_width() {
    CHECK(font5x7::text_width("HELLO") == 29);  // 5 glyphs * 5 + 4 gaps
    CHECK(font5x7::text_width("H") == 5);
    CHECK(font5x7::text_width("") == 0);
    CHECK(font5x7::text_width(nullptr) == 0);
}

static void test_draw_hello() {
    int unsupported = -1;
    Pixels px = render("HELLO", 0, 0, &unsupported);
    CHECK(unsupported == 0);
    CHECK(px.size() == 73);  // H17 + E18 + L11 + L11 + O16
    for (int y = 0; y < 7; ++y) CHECK(px.count({0, y}) == 1);  // H left stem
    CHECK(px.count({6, 0}) == 1);    // E starts at x=6, top-left
    CHECK(px.count({10, 0}) == 1);   // E right column: top
    CHECK(px.count({10, 6}) == 1);   // E right column: bottom
    CHECK(px.count({10, 3}) == 0);   // E right column: middle is empty
}

static void test_draw_offset() {
    int unsupported = -1;
    Pixels px = render("H", 10, 20, &unsupported);
    CHECK(unsupported == 0);
    CHECK(px.count({10, 20}) == 1);
    CHECK(px.count({14, 26}) == 1);
    CHECK(px.count({0, 0}) == 0);
}

static void test_draw_unsupported_char() {
    int unsupported = -1;
    Pixels px = render("H#", 0, 0, &unsupported);
    CHECK(unsupported == 1);
    CHECK(px.size() == 17);  // only H is drawn, '#' is a blank cell
    CHECK(font5x7::text_width("H#") == 11);
}

static void test_no_gif_glyphs() {
    for (char c : {'N', 'G', 'I', 'F'}) CHECK(font5x7::glyph(c) != nullptr);
}

static void test_identify_glyphs() {
    for (char c = '0'; c <= '9'; ++c) CHECK(font5x7::glyph(c) != nullptr);
    CHECK(font5x7::glyph('^') != nullptr);
}

static void test_draw_null() {
    int unsupported = -1;
    Pixels px = render(nullptr, 0, 0, &unsupported);
    CHECK(unsupported == 0);
    CHECK(px.empty());
}

int main() {
    test_glyph_lookup();
    test_glyph_unsupported();
    test_text_width();
    test_draw_hello();
    test_draw_offset();
    test_draw_unsupported_char();
    test_draw_null();
    test_no_gif_glyphs();
    test_identify_glyphs();
    if (g_failures != 0) {
        std::printf("%d check(s) failed\n", g_failures);
        return 1;
    }
    std::printf("all font tests passed\n");
    return 0;
}
