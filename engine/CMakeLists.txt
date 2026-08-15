# engine/CMakeLists.txt
cmake_minimum_required(VERSION 3.24)
project(window_control_engine CXX)

set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

if(NOT WIN32)
  message(FATAL_ERROR "engine/ only builds on Windows (WGC + NVENC are Windows-only APIs)")
endif()

find_package(libdatachannel CONFIG REQUIRED)

enable_testing()
find_package(GTest CONFIG REQUIRED)

add_library(engine_core
  src/capture.cpp
)
target_include_directories(engine_core PUBLIC src)
target_link_libraries(engine_core PUBLIC
  LibDataChannel::LibDataChannel
  windowsapp.lib
  d3d11.lib
  dxgi.lib
)

add_executable(engine_tests test/test_capture.cpp)
target_link_libraries(engine_tests PRIVATE engine_core GTest::gtest GTest::gtest_main)
add_test(NAME engine_tests COMMAND engine_tests)
