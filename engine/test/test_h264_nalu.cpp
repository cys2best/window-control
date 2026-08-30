#include <gtest/gtest.h>
#include "h264_nalu.h"

#include <cstdint>
#include <initializer_list>
#include <vector>

using h264::ContainsNaluType;
using h264::NaluType;
using h264::SpsPpsCache;

namespace {

std::vector<std::uint8_t> Nalu4(
    std::uint8_t header,
    std::initializer_list<std::uint8_t> payload = {}) {
    std::vector<std::uint8_t> result = {0x00, 0x00, 0x00, 0x01, header};
    result.insert(result.end(), payload);
    return result;
}

std::vector<std::uint8_t> Nalu3(
    std::uint8_t header,
    std::initializer_list<std::uint8_t> payload = {}) {
    std::vector<std::uint8_t> result = {0x00, 0x00, 0x01, header};
    result.insert(result.end(), payload);
    return result;
}

std::vector<std::uint8_t> Join(
    std::initializer_list<std::vector<std::uint8_t>> parts) {
    std::vector<std::uint8_t> result;
    for (const auto& part : parts) {
        result.insert(result.end(), part.begin(), part.end());
    }
    return result;
}

} // namespace

TEST(H264Nalu, ScansEveryNaluWithMixedStartCodeLengths) {
    auto payload = Join({
        Nalu4(0x09, {0xF0}),
        Nalu3(0x67, {0x42, 0xC0, 0x29}),
        Nalu4(0x68, {0xCE, 0x3C, 0x80}),
        Nalu3(0x65, {0xAA, 0xBB}),
    });

    EXPECT_TRUE(ContainsNaluType(payload.data(), payload.size(), NaluType::Sps));
    EXPECT_TRUE(ContainsNaluType(payload.data(), payload.size(), NaluType::Pps));
    EXPECT_TRUE(ContainsNaluType(payload.data(), payload.size(), NaluType::Idr));
    EXPECT_FALSE(ContainsNaluType(payload.data(), payload.size(), NaluType::Slice));
}

TEST(H264Nalu, RejectsMissingOrTruncatedStartCode) {
    std::vector<std::uint8_t> noStartCode = {0x67, 0x42, 0xC0, 0x29};
    std::vector<std::uint8_t> noHeader = {0x00, 0x00, 0x00, 0x01};

    EXPECT_FALSE(ContainsNaluType(
        noStartCode.data(), noStartCode.size(), NaluType::Sps));
    EXPECT_FALSE(ContainsNaluType(
        noHeader.data(), noHeader.size(), NaluType::Sps));
    EXPECT_FALSE(ContainsNaluType(nullptr, 0, NaluType::Sps));
}

TEST(SpsPpsCache, RequiresBothSpsAndPps) {
    SpsPpsCache cache;
    auto sps = Nalu4(0x67, {0x42, 0xC0, 0x29});
    auto pps = Nalu4(0x68, {0xCE, 0x3C, 0x80});

    EXPECT_FALSE(cache.HasConfig());
    EXPECT_FALSE(cache.ObserveAndPrepare(sps.data(), sps.size()).has_value());
    EXPECT_FALSE(cache.HasConfig());
    EXPECT_FALSE(cache.ObserveAndPrepare(pps.data(), pps.size()).has_value());
    EXPECT_TRUE(cache.HasConfig());
}

TEST(SpsPpsCache, ConfigObservationSurvivesDroppedStartupPayload) {
    SpsPpsCache cache;
    auto startupConfig = Join({
        Nalu4(0x67, {0x42, 0xC0, 0x29}),
        Nalu4(0x68, {0xCE, 0x3C, 0x80}),
    });
    auto idr = Nalu4(0x65, {0xAA, 0xBB});

    // The caller may drop startupConfig because its track is closed. Cache
    // observation must still make the later IDR independently decodable.
    EXPECT_FALSE(cache.ObserveAndPrepare(
        startupConfig.data(), startupConfig.size()).has_value());

    auto prepared = cache.ObserveAndPrepare(idr.data(), idr.size());
    ASSERT_TRUE(prepared.has_value());
    EXPECT_EQ(*prepared, Join({
        Nalu4(0x67, {0x42, 0xC0, 0x29}),
        Nalu4(0x68, {0xCE, 0x3C, 0x80}),
        idr,
    }));
}

TEST(SpsPpsCache, CombinesParameterSetsObservedInSeparatePayloads) {
    SpsPpsCache cache;
    auto sps = Nalu3(0x67, {0x42, 0xC0, 0x29});
    auto pps = Nalu4(0x68, {0xCE, 0x3C, 0x80});
    auto idr = Nalu4(0x65, {0x10, 0x20});

    cache.ObserveAndPrepare(sps.data(), sps.size());
    cache.ObserveAndPrepare(pps.data(), pps.size());
    auto prepared = cache.ObserveAndPrepare(idr.data(), idr.size());

    ASSERT_TRUE(prepared.has_value());
    EXPECT_EQ(*prepared, Join({sps, pps, idr}));
}

TEST(SpsPpsCache, KeepsPpsWhenARepeatedSpsArrives) {
    SpsPpsCache cache;
    auto firstSps = Nalu4(0x67, {0x42, 0xC0, 0x1F});
    auto latestSps = Nalu4(0x67, {0x42, 0xC0, 0x29});
    auto pps = Nalu4(0x68, {0xCE, 0x3C, 0x80});
    auto idr = Nalu4(0x65, {0x30, 0x40});

    cache.ObserveAndPrepare(firstSps.data(), firstSps.size());
    cache.ObserveAndPrepare(pps.data(), pps.size());
    cache.ObserveAndPrepare(latestSps.data(), latestSps.size());
    auto prepared = cache.ObserveAndPrepare(idr.data(), idr.size());

    ASSERT_TRUE(prepared.has_value());
    EXPECT_EQ(*prepared, Join({latestSps, pps, idr}));
}

TEST(SpsPpsCache, DetectsIdrAfterAudAndPrependsOneCompleteConfig) {
    SpsPpsCache cache;
    auto sps = Nalu4(0x67, {0x42, 0xC0, 0x29});
    auto pps = Nalu4(0x68, {0xCE, 0x3C, 0x80});
    auto accessUnit = Join({Nalu4(0x09, {0xF0}), Nalu4(0x65, {0x50})});

    cache.ObserveAndPrepare(sps.data(), sps.size());
    cache.ObserveAndPrepare(pps.data(), pps.size());
    auto prepared = cache.ObserveAndPrepare(
        accessUnit.data(), accessUnit.size());

    ASSERT_TRUE(prepared.has_value());
    EXPECT_EQ(*prepared, Join({sps, pps, accessUnit}));
}

TEST(SpsPpsCache, LeavesAlreadyConfiguredIdrUnchanged) {
    SpsPpsCache cache;
    auto configuredIdr = Join({
        Nalu4(0x67, {0x42, 0xC0, 0x29}),
        Nalu4(0x68, {0xCE, 0x3C, 0x80}),
        Nalu4(0x65, {0x60}),
    });

    auto prepared = cache.ObserveAndPrepare(
        configuredIdr.data(), configuredIdr.size());

    EXPECT_FALSE(prepared.has_value());
    EXPECT_TRUE(cache.HasConfig());
}

TEST(SpsPpsCache, LeavesNonIdrPayloadUnchanged) {
    SpsPpsCache cache;
    auto config = Join({
        Nalu4(0x67, {0x42, 0xC0, 0x29}),
        Nalu4(0x68, {0xCE, 0x3C, 0x80}),
    });
    auto slice = Nalu4(0x61, {0x70});

    cache.ObserveAndPrepare(config.data(), config.size());

    EXPECT_FALSE(cache.ObserveAndPrepare(
        slice.data(), slice.size()).has_value());
}
