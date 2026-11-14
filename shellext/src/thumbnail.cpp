// Explorer thumbnails for PDF files: the first page, rendered by Windows.Data.Pdf.
#include <windows.h>
#include <shlwapi.h>
#include <thumbcache.h>

#include <new>
#include <vector>

#include "module.h"
#include "pdfrender.h"

namespace paperleaf {

class Thumbnail final : public IInitializeWithStream, public IThumbnailProvider {
public:
    Thumbnail() { AddObject(); }

    // IUnknown
    IFACEMETHODIMP QueryInterface(REFIID riid, void** ppv) override {
        static const QITAB qit[] = {
            QITABENT(Thumbnail, IInitializeWithStream),
            QITABENT(Thumbnail, IThumbnailProvider),
            {nullptr, 0},
        };
        return QISearch(this, qit, riid, ppv);
    }
    IFACEMETHODIMP_(ULONG) AddRef() override { return ++refs_; }
    IFACEMETHODIMP_(ULONG) Release() override {
        ULONG n = --refs_;
        if (n == 0) delete this;
        return n;
    }

    // IInitializeWithStream
    IFACEMETHODIMP Initialize(IStream* stream, DWORD) override {
        if (!data_.empty()) return HRESULT_FROM_WIN32(ERROR_ALREADY_INITIALIZED);
        return ReadAll(stream, data_);
    }

    // IThumbnailProvider
    IFACEMETHODIMP GetThumbnail(UINT cx, HBITMAP* bitmap, WTS_ALPHATYPE* alpha) override {
        if (!bitmap || !alpha) return E_POINTER;
        *bitmap = nullptr;
        *alpha = WTSAT_RGB;
        PdfRenderer renderer;
        HRESULT hr = renderer.Open(data_);
        if (FAILED(hr)) return hr;
        SIZE s = renderer.PageSize(0);
        if (s.cx <= 0 || s.cy <= 0) return E_FAIL;
        // the longer side is cx pixels
        UINT w = cx, h = cx;
        if (s.cx >= s.cy) h = static_cast<UINT>(MulDiv(cx, s.cy, s.cx));
        else w = static_cast<UINT>(MulDiv(cx, s.cx, s.cy));
        return renderer.Render(0, w ? w : 1, h ? h : 1, bitmap);
    }

private:
    ~Thumbnail() { ReleaseObject(); }
    std::atomic<ULONG> refs_{1};
    std::vector<BYTE> data_;
};

HRESULT CreateThumbnail(REFIID riid, void** ppv) {
    auto* obj = new (std::nothrow) Thumbnail();
    if (!obj) return E_OUTOFMEMORY;
    HRESULT hr = obj->QueryInterface(riid, ppv);
    obj->Release();
    return hr;
}

}  // namespace paperleaf
