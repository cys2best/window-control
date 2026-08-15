#include "encoder.h"
#include <nvEncodeAPI.h>
#include <d3d11.h>
#include <vector>
#include <cstring>

struct NvencEncoder::Impl {
    int width, height, fps, bitrateKbps;
    NaluCallback onNalu;

    ID3D11Device* d3dDevice = nullptr;
    ID3D11DeviceContext* d3dContext = nullptr;
    void* nvencEncoder = nullptr; // NV_ENCODE_API_FUNCTION_LIST handle target
    NV_ENCODE_API_FUNCTION_LIST nvenc{};

    ~Impl() {
        if (nvencEncoder && nvenc.nvEncDestroyEncoder) {
            nvenc.nvEncDestroyEncoder(nvencEncoder);
        }
        if (d3dContext) d3dContext->Release();
        if (d3dDevice) d3dDevice->Release();
    }
};

NvencEncoder::NvencEncoder(int width, int height, int fps, int bitrateKbps)
    : impl_(std::make_unique<Impl>()) {
    if (width <= 0) throw std::runtime_error("NvencEncoder: width must be > 0");
    if (height <= 0) throw std::runtime_error("NvencEncoder: height must be > 0");

    impl_->width = width;
    impl_->height = height;
    impl_->fps = fps;
    impl_->bitrateKbps = bitrateKbps;

    D3D_FEATURE_LEVEL featureLevel;
    if (FAILED(D3D11CreateDevice(
            nullptr, D3D_DRIVER_TYPE_HARDWARE, nullptr, 0,
            nullptr, 0, D3D11_SDK_VERSION,
            &impl_->d3dDevice, &featureLevel, &impl_->d3dContext))) {
        throw std::runtime_error("NvencEncoder: D3D11CreateDevice failed (no compatible GPU)");
    }

    impl_->nvenc.version = NV_ENCODE_API_FUNCTION_LIST_VER;
    if (NvEncodeAPICreateInstance(&impl_->nvenc) != NV_ENC_SUCCESS) {
        throw std::runtime_error("NvencEncoder: NvEncodeAPICreateInstance failed (driver too old or no NVENC support)");
    }

    NV_ENC_OPEN_ENCODE_SESSION_EX_PARAMS openParams{};
    openParams.version = NV_ENC_OPEN_ENCODE_SESSION_EX_PARAMS_VER;
    openParams.deviceType = NV_ENC_DEVICE_TYPE_DIRECTX;
    openParams.device = impl_->d3dDevice;
    openParams.apiVersion = NVENCAPI_VERSION;

    if (impl_->nvenc.nvEncOpenEncodeSessionEx(&openParams, &impl_->nvencEncoder) != NV_ENC_SUCCESS) {
        throw std::runtime_error("NvencEncoder: nvEncOpenEncodeSessionEx failed (session limit reached? cap is 3 on stock consumer drivers)");
    }

    NV_ENC_INITIALIZE_PARAMS initParams{};
    initParams.version = NV_ENC_INITIALIZE_PARAMS_VER;
    initParams.encodeGUID = NV_ENC_CODEC_H264_GUID;
    initParams.presetGUID = NV_ENC_PRESET_P4_GUID; // low-latency preset
    initParams.encodeWidth = width;
    initParams.encodeHeight = height;
    initParams.darWidth = width;
    initParams.darHeight = height;
    initParams.frameRateNum = fps;
    initParams.frameRateDen = 1;
    initParams.enablePTD = 1;

    NV_ENC_CONFIG config{};
    config.version = NV_ENC_CONFIG_VER;
    config.rcParams.rateControlMode = NV_ENC_PARAMS_RC_CBR;
    config.rcParams.averageBitRate = bitrateKbps * 1000;
    initParams.encodeConfig = &config;

    if (impl_->nvenc.nvEncInitializeEncoder(impl_->nvencEncoder, &initParams) != NV_ENC_SUCCESS) {
        throw std::runtime_error("NvencEncoder: nvEncInitializeEncoder failed");
    }
}

NvencEncoder::~NvencEncoder() = default;

void NvencEncoder::SetCallback(NaluCallback onNalu) {
    impl_->onNalu = std::move(onNalu);
}

void NvencEncoder::EncodeFrame(const uint8_t* bgraData, int strideBytes) {
    // NOTE: this PoC path re-uploads a CPU BGRA buffer to a fresh D3D11
    // texture each frame (simple, correct, not the zero-copy path). A later
    // optimization pass can hand WindowCapture's GPU texture directly to
    // NVENC's registered-resource API to skip the CPU round-trip entirely
    // (tracked as a Phase 6 hardening item, not required for the PoC's
    // correctness goal).
    //
    // TODO(Task 6 follow-up, requires NVIDIA Video Codec SDK in hand):
    // The actual D3D11 submission path is intentionally not implemented
    // here. Per the task brief, it should follow NVIDIA's own
    // `NvEncoderD3D11` sample class (Apache-2.0, ships in the Video Codec
    // SDK's `Samples/Utils/NvEncoderD3D11.{h,cpp}`), copied into
    // `engine/third_party/nvenc_samples/` and included as
    // "nvenc_samples/NvEncoderD3D11.h". Steps to implement:
    //   1. Build a temporary D3D11 upload texture sized width x height,
    //      format DXGI_FORMAT_B8G8R8A8_UNORM, and copy `bgraData`
    //      (respecting `strideBytes`) into it via Map/Unmap or
    //      UpdateSubresource.
    //   2. Call the sample class's
    //      `EncodeFrame(std::vector<std::vector<uint8_t>>& packets)`,
    //      which internally drives RegisterResource, MapInputResource,
    //      NV_ENC_PIC_PARAMS setup, nvEncEncodePicture, and bitstream
    //      lock/unlock.
    //   3. For each packet in the returned vector, invoke
    //      `onNalu(packet.data(), packet.size())`.
    //
    // This body is deliberately left unimplemented rather than guessing at
    // unverified NVIDIA SDK API signatures without the SDK headers on hand
    // to check against (see task-6-brief.md's "Note on Step 4 for the
    // implementer").
    (void)bgraData;
    (void)strideBytes;
}
