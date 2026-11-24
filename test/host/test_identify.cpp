// Host-side unit tests for main/identify (panel identification screen). Run from the repo root:
//   g++ -std=c++17 -Wall -Wextra -Werror -I main test/host/test_identify.cpp main/identify.cpp main/font5x7.cpp -o /tmp/t && /tmp/t
#include <cstdint>
#include <cstdio>
#include <vector>

#include "identify.h"

static int g_failures = 0;
#define CHECK(cond)                                                    \
    do {                                                               \
        if (!(cond)) {                                                 \
            std::printf("FAIL %s:%d: %s\n", __FILE__, __LINE__, #cond); \
            ++g_failures;                                              \
        }                                                              \
    } while (0)

namespace {

constexpr int kW = 64;
constexpr int kH = 32;

struct Rgb {
    uint8_t r, g, b;
    bool set;
};

struct Canvas {
    int width;
    std::vector<Rgb> px;
    int out_of_range = 0;
    explicit Canvas(int n) : width(kW * n), px(static_cast<size_t>(kW * n * kH), Rgb{0, 0, 0, false}) {}
    const Rgb &at(int x, int y) const { return px[static_cast<size_t>(y * width + x)]; }
    bool lit(int x, int y) const {
        const Rgb &c = at(x, y);
        return c.r != 0 || c.g != 0 || c.b != 0;
    }
};

Canvas render(int n) {
    Canvas c(n);
    identify::draw(n, [&c](int x, int y, uint8_t r, uint8_t g, uint8_t b) {
        if (x < 0 || x >= c.width || y < 0 || y >= kH) {
            ++c.out_of_range;
            return;
        }
        c.px[static_cast<size_t>(y * c.width + x)] = Rgb{r, g, b, true};
    });
    return c;
}

bool is_border_blue(const Rgb &c) { return c.r == 0 && c.g == 80 && c.b == 255; }

}  // namespace

static void test_border() {
    const Canvas c = render(2);
    CHECK(c.out_of_range == 0);
    CHECK(is_border_blue(c.at(0, 0)));
    CHECK(is_border_blue(c.at(63, 0)));
    CHECK(is_border_blue(c.at(0, 31)));
    CHECK(is_border_blue(c.at(63, 31)));
    CHECK(is_border_blue(c.at(64, 0)));   // panel 2 border starts at x=64
    CHECK(is_border_blue(c.at(64, 15)));
    CHECK(is_border_blue(c.at(127, 15)));
    CHECK(is_border_blue(c.at(100, 31)));
    CHECK(!c.lit(1, 1));   // just inside the border is background
    CHECK(!c.lit(65, 1));
}

static void test_whole_canvas_painted() {
    const Canvas c = render(2);
    int unset = 0;
    for (const Rgb &p : c.px) unset += p.set ? 0 : 1;
    CHECK(unset == 0);  // background is painted too, so no stale frame shows through
}

static int lit_in(const Canvas &c, int x0, int x1, int y0, int y1) {
    int count = 0;
    for (int y = y0; y <= y1; ++y)
        for (int x = x0; x <= x1; ++x) count += c.lit(x, y) ? 1 : 0;
    return count;
}

static void test_numbers() {
    const Canvas c = render(2);
    // 3x digit at local (6,5): 15x21 px -> lx 6..20, ly 5..25.
    for (int i = 0; i < 2; ++i) {
        const int ox = kW * i;
        CHECK(lit_in(c, ox + 6, ox + 20, 5, 25) > 20);
        CHECK(lit_in(c, ox + 2, ox + 5, 2, 29) == 0);    // nothing left of the digit
        CHECK(lit_in(c, ox + 21, ox + 39, 2, 29) == 0);  // gap between digit and arrow
    }
    // "1" and "2" differ: the panel numbers are not the same picture.
    int diff = 0;
    for (int y = 5; y <= 25; ++y)
        for (int x = 6; x <= 20; ++x) diff += (c.lit(x, y) != c.lit(x + kW, y)) ? 1 : 0;
    CHECK(diff > 10);
}

static void test_arrow_points_up() {
    const Canvas c = render(2);
    for (int i = 0; i < 2; ++i) {
        const int ox = kW * i;
        int top = -1, bottom = -1;
        for (int y = 1; y <= 30; ++y) {
            int row = 0;
            for (int x = ox + 40; x <= ox + 56; ++x) row += c.lit(x, y) ? 1 : 0;
            if (row > 0 && top < 0) top = y;
            if (row > 0) bottom = y;
        }
        CHECK(top >= 1 && bottom > top + 10);
        if (top < 0) continue;
        int top_w = 0, widest = 0;
        for (int y = top; y <= bottom; ++y) {
            int row = 0;
            for (int x = ox + 40; x <= ox + 56; ++x) row += c.lit(x, y) ? 1 : 0;
            if (y == top) top_w = row;
            if (row > widest) widest = row;
        }
        CHECK(top_w > 0 && top_w < widest);  // the tip (narrowest) is at the smallest ly
        // The tip is centred in x=40..56.
        for (int x = ox + 40; x <= ox + 56; ++x) {
            if (c.lit(x, top)) CHECK(x >= ox + 46 && x <= ox + 50);
        }
        // Bottom row is narrower than the widest row (shaft), so the head is near ly=0.
        int bottom_w = 0;
        for (int x = ox + 40; x <= ox + 56; ++x) bottom_w += c.lit(x, bottom) ? 1 : 0;
        CHECK(bottom_w < widest);
    }
}

static void test_eight_panels() {
    const Canvas c = render(8);
    CHECK(c.out_of_range == 0);
    for (int i = 0; i < 8; ++i) {
        CHECK(is_border_blue(c.at(kW * i, 0)));
        CHECK(is_border_blue(c.at(kW * i + 63, 31)));
        CHECK(lit_in(c, kW * i + 6, kW * i + 20, 5, 25) > 10);
    }
}

static void test_invalid_n_draws_nothing() {
    int calls = 0;
    identify::draw(0, [&calls](int, int, uint8_t, uint8_t, uint8_t) { ++calls; });
    identify::draw(9, [&calls](int, int, uint8_t, uint8_t, uint8_t) { ++calls; });
    CHECK(calls == 0);
}

int main() {
    test_border();
    test_whole_canvas_painted();
    test_numbers();
    test_arrow_points_up();
    test_eight_panels();
    test_invalid_n_draws_nothing();
    if (g_failures != 0) {
        std::printf("%d check(s) failed\n", g_failures);
        return 1;
    }
    std::printf("all identify tests passed\n");
    return 0;
}
