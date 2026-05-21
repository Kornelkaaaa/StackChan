# Stack-chan voice agent — ESP-IDF project entry.
#
# Build with:
#     idf.py set-target esp32s3
#     idf.py menuconfig     # set WiFi SSID/password + backend URL
#     idf.py build flash monitor
#
# Requires ESP-IDF v5.3 or newer (matches the existing M5Stack StackChan
# firmware in ../../../../firmware).

cmake_minimum_required(VERSION 3.16)

include($ENV{IDF_PATH}/tools/cmake/project.cmake)
project(voice_agent)
