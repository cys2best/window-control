#pragma once
#include <windows.h>
#include <functional>
#include <memory>
#include <stdexcept>
#include <string>

class WindowCapture {
public:
    using FrameCallback = std::function<void(const uint8_t* bgraData, int width, int height, int strideBytes)>;

    explicit WindowCapture(HWND hwnd);
    ~WindowCapture();

    WindowCapture(const WindowCapture&) = delete;
    WindowCapture& operator=(const WindowCapture&) = delete;

    void Start(FrameCallback onFrame);
    void Stop();

    static HWND FindWindowByTitleSubstring(const std::wstring& titleSubstring);

private:
    struct Impl;
    std::unique_ptr<Impl> impl_;
};
