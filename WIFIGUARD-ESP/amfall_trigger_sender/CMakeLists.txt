cmake_minimum_required(VERSION 3.16)

include($ENV{IDF_PATH}/tools/cmake/project.cmake)
project(wifi_guard_esp32_c5_amfall_tx)

if(NOT IDF_TARGET STREQUAL "esp32c5")
    message(FATAL_ERROR "This firmware supports only IDF_TARGET=esp32c5")
endif()
