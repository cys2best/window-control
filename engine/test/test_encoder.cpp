#include <gtest/gtest.h>
#include "encoder.h"

TEST(NvencEncoder, ThrowsOnZeroWidth) {
    EXPECT_THROW(NvencEncoder(0, 720, 60, 8000), std::runtime_error);
}

TEST(NvencEncoder, ThrowsOnZeroHeight) {
    EXPECT_THROW(NvencEncoder(1280, 0, 60, 8000), std::runtime_error);
}

TEST(NvencEncoder, ConstructsAndEncodesOneFrameOnAvailableGpu) {
    std::unique_ptr<NvencEncoder> encoder;
    try {
        encoder = std::make_unique<NvencEncoder>(1280, 720, 60, 8000);
    } catch (const std::runtime_error& e) {
        GTEST_SKIP() << "No NVENC-capable GPU available: " << e.what();
    }

    bool gotNalu = false;
    encoder->SetCallback([&](const uint8_t* data, size_t size) {
        if (size > 0) gotNalu = true;
    });

    std::vector<uint8_t> blackFrame(1280 * 720 * 4, 0);
    encoder->EncodeFrame(blackFrame.data(), 1280 * 4);

    EXPECT_TRUE(gotNalu);
}
