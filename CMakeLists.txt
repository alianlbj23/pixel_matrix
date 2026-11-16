# pixel-board: Waveshare ESP32-S3-RGB-Matrix + 64x32 HUB75 panel
# Requires ESP-IDF v5.5.x (the HUB75 library does not build on IDF 6.x yet).
cmake_minimum_required(VERSION 3.16)

include($ENV{IDF_PATH}/tools/cmake/project.cmake)
project(pixel-board)
