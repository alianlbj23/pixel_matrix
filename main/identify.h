// Panel identification screen: each chained panel shows its number, an arrow toward its "up"
// edge (panel-local ly = 0) and a blue border. Pure C++, host-testable.
#pragma once

#include <cstdint>
#include <functional>

namespace identify {

using SetPixel = std::function<void(int x, int y, uint8_t r, uint8_t g, uint8_t b)>;

// Paints the whole 64*n x 32 chain canvas (panel i at x = 64i..64i+63), background included.
// n outside 1..kMaxPanels draws nothing.
void draw(int n, const SetPixel &set_pixel);

}  // namespace identify
