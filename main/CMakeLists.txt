idf_component_register(SRCS "gif_anim_data.cpp" "main.cpp" "font5x7.cpp" "tone.cpp" "audio.cpp"
                            "media.cpp" "mp3_player.cpp"
                       INCLUDE_DIRS "."
                       REQUIRES ESP32-HUB75-MatrixPanel-DMA esp_codec_dev esp_driver_i2c esp_driver_i2s
                                esp_audio_codec spiffs)

# Pack assets/audio/ (MP3 files) into the "audio" SPIFFS partition; `idf.py flash` writes it too.
# The build fails if the files do not fit in the partition (12 MB, see partitions.csv).
spiffs_create_partition_image(audio ${PROJECT_DIR}/assets/audio FLASH_IN_PROJECT)
