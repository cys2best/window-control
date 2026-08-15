# engine/CMakeLists.txt
cmake_minimum_required(VERSION 3.24)
project(window_control_engine CXX)

set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

if(NOT WIN32)
  message(FATAL_ERROR "engine/ only builds on Windows (matches the Host PC's existing Python app)")
endif()

find_package(libdatachannel CONFIG REQUIRED)

enable_testing()
find_package(GTest CONFIG REQUIRED)

add_library(engine_core
  src/scrcpy_video.cpp
)
target_include_directories(engine_core PUBLIC src)
target_link_libraries(engine_core PUBLIC
  LibDataChannel::LibDataChannel
  ws2_32.lib
)

add_executable(engine_tests test/test_scrcpy_video.cpp)
target_link_libraries(engine_tests PRIVATE engine_core GTest::gtest GTest::gtest_main)
add_test(NAME engine_tests COMMAND engine_tests)
