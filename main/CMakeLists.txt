idf_component_register(SRCS "main.cpp" "font5x7.cpp" "tone.cpp" "audio.cpp"
                            "media.cpp" "mp3_player.cpp" "pxg.cpp" "pxa.cpp" "crc32.cpp"
                       INCLUDE_DIRS "."
                       REQUIRES ESP32-HUB75-MatrixPanel-DMA esp_codec_dev esp_driver_i2c esp_driver_i2s
                                esp_audio_codec esp_partition)

# Developer music: pack assets/audio/*.mp3 into a PXA1 image (tools/pack_audio.py, stdlib only) and
# write it to the "audio" partition on `idf.py flash`. The build fails on invalid MP3s or > 12 MB.
idf_build_get_property(python PYTHON)
set(AUDIO_PXA "${CMAKE_BINARY_DIR}/audio.pxa")
file(GLOB AUDIO_FILES CONFIGURE_DEPENDS "${PROJECT_DIR}/assets/audio/*")
add_custom_command(OUTPUT "${AUDIO_PXA}"
    COMMAND "${python}" "${PROJECT_DIR}/tools/pack_audio.py" "${PROJECT_DIR}/assets/audio" "${AUDIO_PXA}"
    DEPENDS "${PROJECT_DIR}/tools/pack_audio.py" "${PROJECT_DIR}/studio/pixelboard/pxa.py"
            "${PROJECT_DIR}/studio/pixelboard/mp3info.py" ${AUDIO_FILES}
    COMMENT "Packing assets/audio into audio.pxa"
    VERBATIM)
add_custom_target(audio_pxa ALL DEPENDS "${AUDIO_PXA}")
esptool_py_flash_to_partition(flash "audio" "${AUDIO_PXA}")

# Developer animation: menu.sh "Convert GIF" writes assets/gif.pxg (PXG1); `idf.py flash` writes it
# to the "gif" partition. End users get theirs from PixelBoard Studio instead.
if(EXISTS ${PROJECT_DIR}/assets/gif.pxg)
    esptool_py_flash_to_partition(flash "gif" "${PROJECT_DIR}/assets/gif.pxg")
endif()
