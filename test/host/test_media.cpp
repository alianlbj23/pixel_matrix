// Host-side unit tests for main/media (no ESP-IDF needed).
// Run: menu.sh "Host unit tests", or
//   g++ -std=c++17 -Wall -Wextra -Werror -I main test/host/test_media.cpp main/media.cpp -o /tmp/test_media && /tmp/test_media
#include <cstdio>
#include <string>
#include <vector>

#include "media.h"

static int g_failures = 0;

#define CHECK(cond)                                                    \
    do {                                                               \
        if (!(cond)) {                                                 \
            std::printf("FAIL %s:%d: %s\n", __FILE__, __LINE__, #cond); \
            ++g_failures;                                              \
        }                                                              \
    } while (0)

using Names = std::vector<std::string>;

static void test_playlist_filters_and_sorts() {
    const Names in = {"b_song.mp3", "readme.txt", "a_song.MP3", "c.Mp3", "cover.jpg", "noext"};
    const Names out = media::make_playlist(in);
    CHECK((out == Names{"a_song.MP3", "b_song.mp3", "c.Mp3"}));
}

static void test_playlist_empty_and_no_mp3() {
    CHECK(media::make_playlist({}).empty());
    CHECK(media::make_playlist({"a.wav", "b.txt"}).empty());
}

static void test_playlist_rejects_bad_names() {
    const Names in = {".mp3", ".hidden.mp3", "dir/inner.mp3", "a.mp3.txt", "x.mp3 ", "", "ok.mp3"};
    CHECK((media::make_playlist(in) == Names{"ok.mp3"}));
}

static void test_playlist_rejects_too_long_names() {
    const std::string longest(media::kMaxNameLen - 4, 'a');  // + ".mp3" == kMaxNameLen
    const std::string too_long(media::kMaxNameLen - 3, 'b');
    const Names out = media::make_playlist({longest + ".mp3", too_long + ".mp3"});
    CHECK((out == Names{longest + ".mp3"}));
}

static void test_playlist_utf8_names_kept() {
    const Names out = media::make_playlist({"\xe6\xad\x8c.mp3"});  // "歌.mp3"
    CHECK(out.size() == 1);
}

static void test_pcm_format_supported() {
    CHECK(media::pcm_format_supported(44100, 2, 16));
    CHECK(media::pcm_format_supported(48000, 1, 16));
    CHECK(media::pcm_format_supported(8000, 1, 16));
    CHECK(media::pcm_format_supported(22050, 2, 16));
}

static void test_pcm_format_rejected() {
    CHECK(!media::pcm_format_supported(0, 2, 16));
    CHECK(!media::pcm_format_supported(96000, 2, 16));  // above codec limit
    CHECK(!media::pcm_format_supported(12345, 2, 16));  // not a standard rate
    CHECK(!media::pcm_format_supported(44100, 0, 16));
    CHECK(!media::pcm_format_supported(44100, 3, 16));
    CHECK(!media::pcm_format_supported(44100, 2, 24));
    CHECK(!media::pcm_format_supported(44100, 2, 8));
}

int main() {
    test_playlist_filters_and_sorts();
    test_playlist_empty_and_no_mp3();
    test_playlist_rejects_bad_names();
    test_playlist_rejects_too_long_names();
    test_playlist_utf8_names_kept();
    test_pcm_format_supported();
    test_pcm_format_rejected();
    if (g_failures == 0) {
        std::printf("test_media: all tests passed\n");
        return 0;
    }
    std::printf("test_media: %d failure(s)\n", g_failures);
    return 1;
}
