// All hardware settings for pixel-board live in this file.
#pragma once

#include <cstdint>

// ---------------------------------------------------------------------------
// HUB75 clock phase. Change this first if the image is shifted or ghosting.
//   false = data clocked on the falling edge (Waveshare official example)
//   true  = data clocked on the rising edge (library default)
// ---------------------------------------------------------------------------
constexpr bool kPanelClkPhase = false;

// Panel resolution (Waveshare RGB-Matrix-P3-64x32). Two panels daisy-chained with a HUB75
// ribbon (panel 1 OUT -> panel 2 IN) form one 128x32 canvas; x 0..63 / 64..127 per panel.
// The chain count and brightness normally come from the PXG1 animation in the `gif` partition
// (written by PixelBoard Studio); kPanelChain / kPanelBrightness are only the fallback used when
// that partition is empty or invalid. menu.sh "Convert GIF" reads kPanelChain as its default.
constexpr uint16_t kPanelWidth = 64;
constexpr uint16_t kPanelHeight = 32;
constexpr uint16_t kPanelChain = 2;

// Most panels the firmware drives in one chain (PXG v2 and "PXB IDENTIFY <n>" accept 1..kMaxPanels).
// Must match MAX_PANELS in studio/pixelboard/layout.py.
constexpr int kMaxPanels = 8;

// Fallback brightness 0-255 (PXG1 allows 10-128). Kept low to limit current draw.
constexpr uint8_t kPanelBrightness = 90;

// HUB75 pins (Waveshare ESP32-S3-RGB-Matrix official mapping, do not reorder)
constexpr int8_t kPinR1 = 4;
constexpr int8_t kPinG1 = 5;
constexpr int8_t kPinB1 = 6;
constexpr int8_t kPinR2 = 7;
constexpr int8_t kPinG2 = 15;
constexpr int8_t kPinB2 = 16;
constexpr int8_t kPinA = 18;
constexpr int8_t kPinB = 8;
constexpr int8_t kPinC = 3;
constexpr int8_t kPinD = 42;
constexpr int8_t kPinE = 9;  // only used by 64-row panels; unused on 64x32
constexpr int8_t kPinLat = 40;
constexpr int8_t kPinOe = 2;
constexpr int8_t kPinClk = 41;

// ---------------------------------------------------------------------------
// Audio: ES8311 codec -> onboard power amp -> speaker connector.
// Pins from Waveshare's official ESP-IDF BSP (esp32_s3_matrix/include/bsp/config.h).
// GPIO43 is also UART0 TX, so the console is moved to USB Serial/JTAG (sdkconfig.defaults).
// ---------------------------------------------------------------------------
constexpr int8_t kPinI2cSda = 47;   // ES8311 control
constexpr int8_t kPinI2cScl = 48;
constexpr int8_t kPinI2sMclk = 12;
constexpr int8_t kPinI2sBclk = 43;
constexpr int8_t kPinI2sWs = 38;
constexpr int8_t kPinI2sDout = 21;  // ESP32 -> ES8311 DAC
constexpr int8_t kPinPaEnable = 11; // power amp enable, active high

constexpr uint32_t kAudioSampleRate = 16000;
// Codec output volume 0-100. Kept moderate for an 8 ohm 5 W speaker.
constexpr int kAudioVolume = 60;
