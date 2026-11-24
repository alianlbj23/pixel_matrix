idf_component_register(SRCS "main.cpp" "font5x7.cpp" "tone.cpp" "audio.cpp"
                            "media.cpp" "mp3_player.cpp" "pxg.cpp" "crc32.cpp"
                       INCLUDE_DIRS "."
                       REQUIRES ESP32-HUB75-MatrixPanel-DMA esp_codec_dev esp_driver_i2c esp_driver_i2s
                                esp_audio_codec spiffs esp_partition)

# Pack assets/audio/ (MP3 files) into the "audio" SPIFFS partition; `idf.py flash` writes it too.
# The build fails if the files do not fit in the partition (12 MB, see partitions.csv).
spiffs_create_partition_image(audio ${PROJECT_DIR}/assets/audio FLASH_IN_PROJECT)

# Developer animation: menu.sh "Convert GIF" writes assets/gif.pxg (PXG1); `idf.py flash` writes it
# to the "gif" partition. End users get theirs from PixelBoard Studio instead.
if(EXISTS ${PROJECT_DIR}/assets/gif.pxg)
    esptool_py_flash_to_partition(flash "gif" "${PROJECT_DIR}/assets/gif.pxg")
endif()
