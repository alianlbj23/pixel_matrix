// Parser for the line-based USB command protocol ("PXB ..." lines from PixelBoard Studio).
// Pure C++, host-testable.
#pragma once

#include <cstddef>

namespace cmd {

// Longest accepted command line, without the line ending.
constexpr size_t kMaxLineLength = 64;

enum class Kind {
    None,      // not a PXB line: ignore silently
    Identify,  // "PXB IDENTIFY <n>", n = 1..kMaxPanels
    Error,     // a PXB line that is malformed or unknown: reply "PXB ERR <reason>"
};

struct Result {
    Kind kind;
    int n;               // panel count, valid for Identify
    const char *reason;  // static one-line text, valid for Error
};

// Leading/trailing spaces, tabs, '\r' and '\n' are ignored. line may be nullptr.
Result parse(const char *line);

}  // namespace cmd
