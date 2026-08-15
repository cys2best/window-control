#include <gtest/gtest.h>
#include "capture.h"

TEST(WindowCapture, FindWindowByTitleSubstring_ReturnsNullWhenNotFound) {
    HWND hwnd = WindowCapture::FindWindowByTitleSubstring(L"____definitely_not_a_real_window_title____");
    EXPECT_EQ(hwnd, nullptr);
}

TEST(WindowCapture, FindWindowByTitleSubstring_FindsDesktopShellOrExplorer) {
    // "Program Manager" is the desktop shell window title, always present on Windows.
    HWND hwnd = WindowCapture::FindWindowByTitleSubstring(L"Program Manager");
    EXPECT_NE(hwnd, nullptr);
}

TEST(WindowCapture, ConstructorThrowsOnNullHwnd) {
    EXPECT_THROW(WindowCapture(nullptr), std::runtime_error);
}
