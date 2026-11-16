#include "media.h"

#include <algorithm>

namespace media {

namespace {

constexpr char kExt[] = ".mp3";
constexpr size_t kExtLen = sizeof(kExt) - 1;

char lower_ascii(char c) { return (c >= 'A' && c <= 'Z') ? static_cast<char>(c - 'A' + 'a') : c; }

bool is_mp3_name(const std::string &name) {
    if (name.size() <= kExtLen || name.size() > kMaxNameLen) return false;
    if (name[0] == '.') return false;  // hidden file
    for (char c : name) {
        if (c == '/' || c == '\\' || static_cast<unsigned char>(c) < 0x20) return false;
    }
    for (size_t i = 0; i < kExtLen; ++i) {
        if (lower_ascii(name[name.size() - kExtLen + i]) != kExt[i]) return false;
    }
    return true;
}

}  // namespace

std::vector<std::string> make_playlist(const std::vector<std::string> &names) {
    std::vector<std::string> out;
    for (const std::string &n : names) {
        if (is_mp3_name(n)) out.push_back(n);
    }
    std::sort(out.begin(), out.end());
    return out;
}

bool pcm_format_supported(uint32_t sample_rate, int channels, int bits_per_sample) {
    static constexpr uint32_t kRates[] = {8000, 11025, 12000, 16000, 22050, 24000, 32000, 44100, 48000};
    const bool rate_ok = std::find(std::begin(kRates), std::end(kRates), sample_rate) != std::end(kRates);
    return rate_ok && (channels == 1 || channels == 2) && bits_per_sample == 16;
}

}  // namespace media
