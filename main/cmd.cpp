#include "cmd.h"

#include <cstring>

#include "board_config.h"

namespace cmd {
namespace {

bool is_space(char c) { return c == ' ' || c == '\t' || c == '\r' || c == '\n'; }

const char *skip_spaces(const char *p) {
    while (*p != '\0' && is_space(*p)) ++p;
    return p;
}

// Copies the next whitespace-separated token into out (truncated to out_size - 1) and returns
// the position after it. *len is the full token length, so callers can reject overlong tokens.
const char *next_token(const char *p, char *out, size_t out_size, size_t *len) {
    p = skip_spaces(p);
    size_t n = 0;
    while (p[n] != '\0' && !is_space(p[n])) ++n;
    const size_t copy = n < out_size - 1 ? n : out_size - 1;
    std::memcpy(out, p, copy);
    out[copy] = '\0';
    *len = n;
    return p + n;
}

Result error(const char *reason) { return Result{Kind::Error, 0, reason}; }

}  // namespace

Result parse(const char *line) {
    if (line == nullptr) return Result{Kind::None, 0, nullptr};
    const char *p = skip_spaces(line);
    if (std::strncmp(p, "PXB", 3) != 0) return Result{Kind::None, 0, nullptr};

    char token[16];
    size_t len = 0;
    p = next_token(p, token, sizeof(token), &len);
    if (std::strcmp(token, "PXB") != 0 || len != 3) return error("unknown command");

    p = next_token(p, token, sizeof(token), &len);
    if (len == 0) return error("missing command");
    if (std::strcmp(token, "IDENTIFY") != 0 || len != 8) return error("unknown command");

    p = next_token(p, token, sizeof(token), &len);
    if (len == 0) return error("missing panel count");
    // Exactly one digit: 1..kMaxPanels is always a single digit (kMaxPanels <= 9).
    static_assert(kMaxPanels >= 1 && kMaxPanels <= 9, "panel count parser expects one digit");
    if (len != 1 || token[0] < '0' || token[0] > '9') return error("panel count must be a number");
    const int n = token[0] - '0';
    if (n < 1 || n > kMaxPanels) return error("panel count out of range");

    next_token(p, token, sizeof(token), &len);
    if (len != 0) return error("too many arguments");
    return Result{Kind::Identify, n, nullptr};
}

}  // namespace cmd
