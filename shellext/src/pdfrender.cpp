#include "pdfrender.h"

#include <unknwn.h>   // before the WinRT headers: classic COM interop
#include <shcore.h>
#include <wincodec.h>

#include <future>

#include <winrt/Windows.Data.Pdf.h>
#include <winrt/Windows.Foundation.h>
#include <winrt/Windows.Storage.Streams.h>
#include <winrt/Windows.UI.h>

#pragma comment(lib, "windowscodecs.lib")
#pragma comment(lib, "shcore.lib")
#pragma comment(lib, "windowsapp.lib")

using namespace winrt;
using namespace winrt::Windows::Data::Pdf;
using namespace winrt::Windows::Storage::Streams;

namespace paperleaf {

HRESULT ReadAll(IStream* stream, std::vector<BYTE>& out, ULONGLONG limit) {
    if (!stream) return E_INVALIDARG;
    STATSTG st{};
    if (SUCCEEDED(stream->Stat(&st, STATFLAG_NONAME)) && st.cbSize.QuadPart > limit) return E_OUTOFMEMORY;
    LARGE_INTEGER zero{};
    stream->Seek(zero, STREAM_SEEK_SET, nullptr);
    out.clear();
    BYTE buf[64 * 1024];
    for (;;) {
        ULONG got = 0;
        HRESULT hr = stream->Read(buf, sizeof(buf), &got);
        if (FAILED(hr)) return hr;
        if (got == 0) break;
        out.insert(out.end(), buf, buf + got);
        if (out.size() > limit) return E_OUTOFMEMORY;
        if (hr == S_FALSE) break;
    }
    return out.empty() ? E_FAIL : S_OK;
}

PdfRenderer::PdfRenderer() : worker_([this] { Loop(); }) {}

PdfRenderer::~PdfRenderer() {
    Run([this] {
        if (doc_) {
            PdfDocument doc{nullptr};
            winrt::attach_abi(doc, doc_);   // releases the document on the worker
            doc_ = nullptr;
        }
    });
    {
        std::lock_guard<std::mutex> g(lock_);
        quit_ = true;
    }
    wake_.notify_one();
    worker_.join();
}

void PdfRenderer::Loop() {
    winrt::init_apartment(winrt::apartment_type::multi_threaded);
    for (;;) {
        std::function<void()> job;
        {
            std::unique_lock<std::mutex> g(lock_);
            wake_.wait(g, [this] { return quit_ || !jobs_.empty(); });
            if (jobs_.empty()) break;
            job = std::move(jobs_.front());
            jobs_.pop();
        }
        job();
    }
    winrt::uninit_apartment();
}

void PdfRenderer::Run(std::function<void()> job) {
    std::promise<void> done;
    auto fut = done.get_future();
    {
        std::lock_guard<std::mutex> g(lock_);
        jobs_.push([&] {
            try { job(); } catch (...) {}
            done.set_value();
        });
    }
    wake_.notify_one();
    fut.wait();
}

HRESULT PdfRenderer::Open(const std::vector<BYTE>& data) {
    HRESULT result = E_FAIL;
    Run([&] {
        try {
            InMemoryRandomAccessStream mem;
            DataWriter writer(mem);
            writer.WriteBytes(array_view<const uint8_t>(data.data(), data.data() + data.size()));
            writer.StoreAsync().get();
            writer.DetachStream();
            mem.Seek(0);
            PdfDocument doc = PdfDocument::LoadFromStreamAsync(mem).get();
            if (doc.IsPasswordProtected()) return;   // never shown without the password
            sizes_.clear();
            for (uint32_t i = 0; i < doc.PageCount(); ++i) {
                auto s = doc.GetPage(i).Size();     // DIPs (1/96 in), rotation applied
                sizes_.push_back({static_cast<LONG>(s.Width * 72.0 / 96.0), static_cast<LONG>(s.Height * 72.0 / 96.0)});
            }
            doc_ = winrt::detach_abi(doc);
            result = sizes_.empty() ? E_FAIL : S_OK;
        } catch (...) {
            result = E_FAIL;
        }
    });
    return result;
}

static HRESULT DecodeToDib(IStream* png, HBITMAP* out) {
    com_ptr<IWICImagingFactory> wic;
    HRESULT hr = CoCreateInstance(CLSID_WICImagingFactory, nullptr, CLSCTX_INPROC_SERVER, IID_PPV_ARGS(wic.put()));
    if (FAILED(hr)) return hr;
    com_ptr<IWICBitmapDecoder> decoder;
    hr = wic->CreateDecoderFromStream(png, nullptr, WICDecodeMetadataCacheOnDemand, decoder.put());
    if (FAILED(hr)) return hr;
    com_ptr<IWICBitmapFrameDecode> frame;
    hr = decoder->GetFrame(0, frame.put());
    if (FAILED(hr)) return hr;
    com_ptr<IWICFormatConverter> conv;
    hr = wic->CreateFormatConverter(conv.put());
    if (FAILED(hr)) return hr;
    hr = conv->Initialize(frame.get(), GUID_WICPixelFormat32bppBGRA, WICBitmapDitherTypeNone, nullptr, 0.0,
                          WICBitmapPaletteTypeCustom);
    if (FAILED(hr)) return hr;
    UINT w = 0, h = 0;
    conv->GetSize(&w, &h);
    BITMAPINFO bi{};
    bi.bmiHeader.biSize = sizeof(bi.bmiHeader);
    bi.bmiHeader.biWidth = static_cast<LONG>(w);
    bi.bmiHeader.biHeight = -static_cast<LONG>(h);   // top-down
    bi.bmiHeader.biPlanes = 1;
    bi.bmiHeader.biBitCount = 32;
    bi.bmiHeader.biCompression = BI_RGB;
    void* bits = nullptr;
    HBITMAP bmp = CreateDIBSection(nullptr, &bi, DIB_RGB_COLORS, &bits, nullptr, 0);
    if (!bmp) return E_OUTOFMEMORY;
    hr = conv->CopyPixels(nullptr, w * 4, w * h * 4, static_cast<BYTE*>(bits));
    if (FAILED(hr)) {
        DeleteObject(bmp);
        return hr;
    }
    *out = bmp;
    return S_OK;
}

HRESULT PdfRenderer::Render(UINT index, UINT width, UINT height, HBITMAP* out) {
    if (!out) return E_POINTER;
    *out = nullptr;
    if (!doc_ || index >= sizes_.size() || width == 0 || height == 0) return E_INVALIDARG;
    HRESULT result = E_FAIL;
    Run([&] {
        try {
            PdfDocument doc{nullptr};
            winrt::copy_from_abi(doc, doc_);
            PdfPage page = doc.GetPage(index);
            PdfPageRenderOptions opts;
            opts.DestinationWidth(width);
            opts.DestinationHeight(height);
            opts.BackgroundColor(winrt::Windows::UI::Color{255, 255, 255, 255});
            InMemoryRandomAccessStream png;
            page.RenderToStreamAsync(png, opts).get();   // PNG
            png.Seek(0);
            com_ptr<IStream> stream;
            HRESULT hr = CreateStreamOverRandomAccessStream(winrt::get_unknown(png), IID_PPV_ARGS(stream.put()));
            if (SUCCEEDED(hr)) hr = DecodeToDib(stream.get(), out);
            result = hr;
        } catch (...) {
            result = E_FAIL;
        }
    });
    return result;
}

}  // namespace paperleaf
