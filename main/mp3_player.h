// Background MP3 playback from the "audio" SPIFFS partition (built from assets/audio/).
#pragma once

namespace mp3_player {

// Mounts the "audio" partition at /audio and starts a task that plays every *.mp3 in
// file-name order, looping forever. audio::init() must have succeeded first, and after
// this returns true the player task owns the audio module. Returns false (logged) on failure.
bool start();

}  // namespace mp3_player
