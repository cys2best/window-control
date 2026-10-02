# engine/CMakeLists.txt
cmake_minimum_required(VERSION 3.24)
project(window_control_engine CXX)

set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

if(MSVC)
  # MSVC's __cplusplus macro is stuck at 199711L unless this is set, no
  # matter what /std: flag is active; library feature detection reads it.
  add_compile_options(/Zc:__cplusplus)
endif()

if(NOT WIN32)
  message(FATAL_ERROR "engine/ only builds on Windows (matches the Host PC's existing Python app)")
endif()

find_package(libdatachannel CONFIG REQUIRED)
find_package(nlohmann_json CONFIG REQUIRED)
find_package(httplib CONFIG REQUIRED)
find_package(OpenSSL REQUIRED)

enable_testing()
find_package(GTest CONFIG REQUIRED)

add_library(engine_core
  src/scrcpy_video.cpp
  src/scrcpy_control.cpp
  src/peer_session.cpp
  src/ice_config.cpp
  src/remote_peer_handler.cpp
  src/peer_registry.cpp
  src/h264_nalu.cpp
  src/http_server.cpp
  src/ready_record.cpp
  src/whep_capability.cpp
  src/whep_handler.cpp
  src/scrcpy_source.cpp
  src/source_video_fanout.cpp
  src/admin_handler.cpp
  src/input_router.cpp
  src/engine_config.cpp
  src/send_pacer.cpp
)
target_include_directories(engine_core PUBLIC src)
target_link_libraries(engine_core PUBLIC
  LibDataChannel::LibDataChannel
  nlohmann_json::nlohmann_json
  httplib::httplib
  OpenSSL::SSL
  OpenSSL::Crypto
  ws2_32.lib
)

add_executable(engine_tests
  test/test_scrcpy_video.cpp
  test/test_scrcpy_control.cpp
  test/test_h264_nalu.cpp
  test/test_http_server.cpp
  test/test_peer_session.cpp
  test/test_ice_config.cpp
  test/test_remote_peer_handler.cpp
  test/test_peer_registry.cpp
  test/test_whep_capability.cpp
  test/test_whep_handler.cpp
  test/test_scrcpy_source.cpp
  test/test_source_video_fanout.cpp
  test/test_admin_handler.cpp
  test/test_input_router.cpp
  test/test_main_wiring.cpp
  test/test_send_pacer.cpp
)
target_link_libraries(engine_tests PRIVATE engine_core GTest::gtest GTest::gtest_main)
add_test(NAME engine_tests COMMAND engine_tests)

add_executable(engine src/main.cpp)
target_link_libraries(engine PRIVATE engine_core)

# engine_core links libdatachannel etc. as shared (.dll) libraries under
# vcpkg's default x64-windows triplet — copy their runtime DLLs next to
# each executable, or Windows fails to launch them with "code execution
# cannot proceed because X.dll was not found".
foreach(exe_target engine engine_tests)
  add_custom_command(TARGET ${exe_target} POST_BUILD
    COMMAND ${CMAKE_COMMAND} -E copy_if_different
      "$<TARGET_RUNTIME_DLLS:${exe_target}>"
      "$<TARGET_FILE_DIR:${exe_target}>"
    COMMAND_EXPAND_LISTS
  )
endforeach()
