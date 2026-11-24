// Host-side unit tests for main/cmd (USB command line parser). Run from the repo root:
//   g++ -std=c++17 -Wall -Wextra -Werror -I main test/host/test_cmd.cpp main/cmd.cpp -o /tmp/t && /tmp/t
#include <cstdio>

#include "cmd.h"

static int g_failures = 0;
#define CHECK(cond)                                                    \
    do {                                                               \
        if (!(cond)) {                                                 \
            std::printf("FAIL %s:%d: %s\n", __FILE__, __LINE__, #cond); \
            ++g_failures;                                              \
        }                                                              \
    } while (0)

static void check_identify(const char *line, int n) {
    const cmd::Result r = cmd::parse(line);
    if (r.kind != cmd::Kind::Identify || r.n != n) {
        std::printf("FAIL identify \"%s\": kind=%d n=%d\n", line, static_cast<int>(r.kind), r.n);
        ++g_failures;
    }
}

static void check_error(const char *line) {
    const cmd::Result r = cmd::parse(line);
    if (r.kind != cmd::Kind::Error || r.reason == nullptr || r.reason[0] == '\0') {
        std::printf("FAIL error \"%s\": kind=%d\n", line, static_cast<int>(r.kind));
        ++g_failures;
    }
}

static void check_none(const char *line) {
    const cmd::Result r = cmd::parse(line);
    if (r.kind != cmd::Kind::None) {
        std::printf("FAIL none \"%s\": kind=%d\n", line ? line : "(null)", static_cast<int>(r.kind));
        ++g_failures;
    }
}

static void test_identify_valid() {
    check_identify("PXB IDENTIFY 1", 1);
    check_identify("PXB IDENTIFY 8", 8);
    check_identify("  PXB IDENTIFY 3\r", 3);
    check_identify("PXB IDENTIFY 2\r\n", 2);
    check_identify("PXB  IDENTIFY\t5 ", 5);
}

static void test_identify_errors() {
    check_error("PXB IDENTIFY 0");
    check_error("PXB IDENTIFY 9");
    check_error("PXB IDENTIFY abc");
    check_error("PXB IDENTIFY");
    check_error("PXB IDENTIFY -1");
    check_error("PXB IDENTIFY 3x");
    check_error("PXB IDENTIFY 08");
    check_error("PXB IDENTIFY 99999999999999999999");
    check_error("PXB IDENTIFY 2 3");
    check_error("PXB FOO");
    check_error("PXB");
    check_error("PXBX IDENTIFY 2");
    check_error("PXB identify 2");
}

static void test_not_for_us() {
    check_none("hello");
    check_none("");
    check_none("   \r");
    check_none(nullptr);
    check_none("pxb IDENTIFY 2");
    check_none("I (123) pixel-board: PXB IDENTIFY 2");
}

static void test_error_reason_is_one_line() {
    const cmd::Result r = cmd::parse("PXB IDENTIFY 9");
    CHECK(r.kind == cmd::Kind::Error);
    if (r.reason != nullptr) {
        for (const char *p = r.reason; *p != '\0'; ++p) CHECK(*p != '\n' && *p != '\r');
    }
}

int main() {
    test_identify_valid();
    test_identify_errors();
    test_not_for_us();
    test_error_reason_is_one_line();
    if (g_failures != 0) {
        std::printf("%d check(s) failed\n", g_failures);
        return 1;
    }
    std::printf("all cmd tests passed\n");
    return 0;
}
