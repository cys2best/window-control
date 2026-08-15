#include "capture.h"
#include <winrt/Windows.Graphics.Capture.h>
#include <winrt/Windows.Graphics.DirectX.Direct3D11.h>
#include <windows.graphics.capture.interop.h>
#include <d3d11.h>
#include <dxgi1_2.h>
#include <vector>
#include <atomic>

namespace winrt {
using namespace Windows::Graphics::Capture;
using namespace Windows::Graphics::DirectX::Direct3D11;
}

HWND WindowCapture::FindWindowByTitleSubstring(const std::wstring& titleSubstring) {
    struct SearchCtx {
        const std::wstring* needle;
        HWND result = nullptr;
    } ctx{ &titleSubstring };

    EnumWindows([](HWND hwnd, LPARAM lparam) -> BOOL {
        auto* ctx = reinterpret_cast<SearchCtx*>(lparam);
        wchar_t title[256];
        int len = GetWindowTextW(hwnd, title, 256);
        if (len > 0) {
            std::wstring t(title, len);
            if (t.find(*ctx->needle) != std::wstring::npos) {
                ctx->result = hwnd;
                return FALSE; // stop enumeration
            }
        }
        return TRUE;
    }, reinterpret_cast<LPARAM>(&ctx));

    return ctx.result;
}

struct WindowCapture::Impl {
    HWND hwnd;
    winrt::com_ptr<ID3D11Device> d3dDevice;
    winrt::IDirect3DDevice winrtDevice{ nullptr };
    winrt::GraphicsCaptureItem item{ nullptr };
    winrt::Direct3D11CaptureFramePool framePool{ nullptr };
    winrt::GraphicsCaptureSession session{ nullptr };
    std::atomic<bool> running{ false };
    FrameCallback onFrame;

    void OnFrameArrived(winrt::Direct3D11CaptureFramePool const& sender, winrt::IInspectable const&) {
        if (!running.load()) return;
        auto frame = sender.TryGetNextFrame();
        if (!frame) return;

        auto surface = frame.Surface();
        auto access = surface.as<::Windows::Graphics::DirectX::Direct3D11::IDirect3DDxgiInterfaceAccess>();
        winrt::com_ptr<ID3D11Texture2D> texture;
        access->GetInterface(IID_PPV_ARGS(texture.put()));

        D3D11_TEXTURE2D_DESC desc;
        texture->GetDesc(&desc);

        // Copy to a CPU-readable staging texture for this PoC (a production
        // path would keep this on the GPU and hand the texture directly to
        // NVENC's D3D11 input; see encoder.cpp TODO in Task 6).
        D3D11_TEXTURE2D_DESC stagingDesc = desc;
        stagingDesc.Usage = D3D11_USAGE_STAGING;
        stagingDesc.BindFlags = 0;
        stagingDesc.CPUAccessFlags = D3D11_CPU_ACCESS_READ;
        stagingDesc.MiscFlags = 0;

        winrt::com_ptr<ID3D11Texture2D> staging;
        if (FAILED(d3dDevice->CreateTexture2D(&stagingDesc, nullptr, staging.put()))) return;

        winrt::com_ptr<ID3D11DeviceContext> ctx;
        d3dDevice->GetImmediateContext(ctx.put());
        ctx->CopyResource(staging.get(), texture.get());

        D3D11_MAPPED_SUBRESOURCE mapped;
        if (SUCCEEDED(ctx->Map(staging.get(), 0, D3D11_MAP_READ, 0, &mapped))) {
            if (onFrame) {
                onFrame(static_cast<const uint8_t*>(mapped.pData), desc.Width, desc.Height, mapped.RowPitch);
            }
            ctx->Unmap(staging.get(), 0);
        }
    }
};

WindowCapture::WindowCapture(HWND hwnd) : impl_(std::make_unique<Impl>()) {
    if (!hwnd) {
        throw std::runtime_error("WindowCapture: hwnd is null");
    }
    impl_->hwnd = hwnd;

    D3D_FEATURE_LEVEL featureLevel;
    if (FAILED(D3D11CreateDevice(
            nullptr, D3D_DRIVER_TYPE_HARDWARE, nullptr,
            D3D11_CREATE_DEVICE_BGRA_SUPPORT,
            nullptr, 0, D3D11_SDK_VERSION,
            impl_->d3dDevice.put(), &featureLevel, nullptr))) {
        throw std::runtime_error("WindowCapture: D3D11CreateDevice failed");
    }

    auto dxgiDevice = impl_->d3dDevice.as<IDXGIDevice>();
    winrt::com_ptr<::IInspectable> winrtDeviceInspectable;
    if (FAILED(CreateDirect3D11DeviceFromDXGIDevice(dxgiDevice.get(),
            reinterpret_cast<IInspectable**>(winrt::put_abi(impl_->winrtDevice))))) {
        throw std::runtime_error("WindowCapture: CreateDirect3D11DeviceFromDXGIDevice failed");
    }

    auto interopFactory = winrt::get_activation_factory<winrt::GraphicsCaptureItem>()
        .as<IGraphicsCaptureItemInterop>();
    if (FAILED(interopFactory->CreateForWindow(hwnd, winrt::guid_of<winrt::GraphicsCaptureItem>(),
            winrt::put_abi(impl_->item)))) {
        throw std::runtime_error("WindowCapture: CreateForWindow failed (invalid HWND or WGC unsupported)");
    }
}

WindowCapture::~WindowCapture() {
    Stop();
}

void WindowCapture::Start(FrameCallback onFrame) {
    impl_->onFrame = std::move(onFrame);
    impl_->framePool = winrt::Direct3D11CaptureFramePool::Create(
        impl_->winrtDevice,
        winrt::Windows::Graphics::DirectX::DirectXPixelFormat::B8G8R8A8UIntNormalized,
        2, impl_->item.Size());

    impl_->framePool.FrameArrived({ impl_.get(), &Impl::OnFrameArrived });
    impl_->session = impl_->framePool.CreateCaptureSession(impl_->item);
    impl_->running = true;
    impl_->session.StartCapture();
}

void WindowCapture::Stop() {
    if (!impl_->running.exchange(false)) return;
    if (impl_->session) impl_->session.Close();
    if (impl_->framePool) impl_->framePool.Close();
}
