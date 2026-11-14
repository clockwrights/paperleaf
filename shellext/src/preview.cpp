// Explorer preview pane for PDF files: every page, scaled to the pane width,
// rendered on demand while you scroll (mouse wheel, scroll bar, keys).
#include <windows.h>
#include <ocidl.h>
#include <shlobj.h>
#include <shlwapi.h>
#include <shobjidl.h>
#include <windowsx.h>

#include <algorithm>
#include <cstdlib>
#include <map>
#include <memory>
#include <vector>

#include "module.h"
#include "pdfrender.h"

namespace paperleaf {

namespace {
const wchar_t kClass[] = L"PaperleafPdfPreview";
const int kGap = 8;            // pixels between pages
const size_t kCacheMax = 12;   // rendered pages kept in memory
}

class Preview final : public IInitializeWithStream, public IPreviewHandler, public IOleWindow,
                      public IObjectWithSite {
public:
    Preview() { AddObject(); }

    // IUnknown
    IFACEMETHODIMP QueryInterface(REFIID riid, void** ppv) override {
        static const QITAB qit[] = {
            QITABENT(Preview, IInitializeWithStream),
            QITABENT(Preview, IPreviewHandler),
            QITABENT(Preview, IOleWindow),
            QITABENT(Preview, IObjectWithSite),
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
    IFACEMETHODIMP Initialize(IStream* stream, DWORD) override { return ReadAll(stream, data_); }

    // IPreviewHandler
    IFACEMETHODIMP SetWindow(HWND parent, const RECT* rc) override {
        if (!parent || !rc) return E_INVALIDARG;
        parent_ = parent;
        rect_ = *rc;
        if (hwnd_) {
            SetParent(hwnd_, parent_);
            Place();
        }
        return S_OK;
    }
    IFACEMETHODIMP SetRect(const RECT* rc) override {
        if (!rc) return E_INVALIDARG;
        rect_ = *rc;
        Place();
        return S_OK;
    }
    IFACEMETHODIMP DoPreview() override {
        if (hwnd_ || !parent_) return E_FAIL;
        renderer_ = std::make_unique<PdfRenderer>();
        HRESULT hr = renderer_->Open(data_);
        if (FAILED(hr)) {
            renderer_.reset();
            return hr;   // the pane shows "no preview available"
        }
        Register();
        hwnd_ = CreateWindowExW(0, kClass, L"", WS_CHILD | WS_VSCROLL | WS_VISIBLE, rect_.left, rect_.top,
                                rect_.right - rect_.left, rect_.bottom - rect_.top, parent_, nullptr, g_module,
                                this);
        if (!hwnd_) return HRESULT_FROM_WIN32(GetLastError());
        Place();
        return S_OK;
    }
    IFACEMETHODIMP Unload() override {
        if (hwnd_) {
            DestroyWindow(hwnd_);
            hwnd_ = nullptr;
        }
        ClearCache();
        renderer_.reset();
        data_.clear();
        return S_OK;
    }
    IFACEMETHODIMP SetFocus() override {
        if (hwnd_) ::SetFocus(hwnd_);
        return S_OK;
    }
    IFACEMETHODIMP QueryFocus(HWND* phwnd) override {
        if (!phwnd) return E_INVALIDARG;
        *phwnd = ::GetFocus();
        return *phwnd ? S_OK : HRESULT_FROM_WIN32(GetLastError());
    }
    IFACEMETHODIMP TranslateAccelerator(MSG* msg) override {
        IPreviewHandlerFrame* frame = nullptr;
        if (site_ && SUCCEEDED(site_->QueryInterface(IID_PPV_ARGS(&frame)))) {
            HRESULT hr = frame->TranslateAccelerator(msg);
            frame->Release();
            return hr;
        }
        return S_FALSE;
    }

    // IOleWindow
    IFACEMETHODIMP GetWindow(HWND* phwnd) override {
        if (!phwnd) return E_INVALIDARG;
        *phwnd = parent_;
        return S_OK;
    }
    IFACEMETHODIMP ContextSensitiveHelp(BOOL) override { return E_NOTIMPL; }

    // IObjectWithSite
    IFACEMETHODIMP SetSite(IUnknown* site) override {
        if (site_) site_->Release();
        site_ = site;
        if (site_) site_->AddRef();
        return S_OK;
    }
    IFACEMETHODIMP GetSite(REFIID riid, void** ppv) override {
        if (!site_) {
            *ppv = nullptr;
            return E_FAIL;
        }
        return site_->QueryInterface(riid, ppv);
    }

private:
    ~Preview() {
        Unload();
        if (site_) site_->Release();
        ReleaseObject();
    }

    static void Register() {
        WNDCLASSEXW wc{sizeof(wc)};
        wc.lpfnWndProc = WndProc;
        wc.hInstance = g_module;
        wc.hCursor = LoadCursor(nullptr, IDC_ARROW);
        wc.hbrBackground = nullptr;
        wc.lpszClassName = kClass;
        RegisterClassExW(&wc);   // fails harmlessly when already registered
    }

    void Place() {
        if (!hwnd_) return;
        SetWindowPos(hwnd_, nullptr, rect_.left, rect_.top, rect_.right - rect_.left, rect_.bottom - rect_.top,
                     SWP_NOZORDER | SWP_NOACTIVATE);
        Layout();
    }

    // page sizes in pixels at the current pane width
    void Layout() {
        tops_.clear();
        heights_.clear();
        RECT rc;
        GetClientRect(hwnd_, &rc);
        width_ = std::max<int>(1, rc.right - rc.left - 2 * kGap);
        int y = kGap;
        for (UINT i = 0; i < renderer_->PageCount(); ++i) {
            SIZE s = renderer_->PageSize(i);
            int h = s.cx > 0 ? MulDiv(width_, s.cy, s.cx) : width_;
            tops_.push_back(y);
            heights_.push_back(h);
            y += h + kGap;
        }
        total_ = y;
        ClearCache();
        SCROLLINFO si{sizeof(si), SIF_RANGE | SIF_PAGE | SIF_POS};
        si.nMin = 0;
        si.nMax = total_;
        si.nPage = rc.bottom - rc.top;
        pos_ = std::min<int>(pos_, std::max<int>(0, total_ - static_cast<int>(si.nPage)));
        si.nPos = pos_;
        SetScrollInfo(hwnd_, SB_VERT, &si, TRUE);
        InvalidateRect(hwnd_, nullptr, FALSE);
    }

    void ClearCache() {
        for (auto& kv : cache_) DeleteObject(kv.second);
        cache_.clear();
    }

    HBITMAP PageBitmap(UINT i) {
        auto it = cache_.find(i);
        if (it != cache_.end()) return it->second;
        HBITMAP bmp = nullptr;
        if (FAILED(renderer_->Render(i, width_, heights_[i], &bmp))) return nullptr;
        if (cache_.size() >= kCacheMax) {   // drop the page farthest away
            auto victim = cache_.begin();
            for (auto c = cache_.begin(); c != cache_.end(); ++c)
                if (std::abs(static_cast<int>(c->first) - static_cast<int>(i)) >
                    std::abs(static_cast<int>(victim->first) - static_cast<int>(i)))
                    victim = c;
            DeleteObject(victim->second);
            cache_.erase(victim);
        }
        cache_[i] = bmp;
        return bmp;
    }

    void Paint(HDC dc) {
        RECT rc;
        GetClientRect(hwnd_, &rc);
        HDC mem = CreateCompatibleDC(dc);
        HBITMAP back = CreateCompatibleBitmap(dc, rc.right, rc.bottom);
        HGDIOBJ oldBack = SelectObject(mem, back);
        HBRUSH bg = CreateSolidBrush(RGB(0xE6, 0xE6, 0xE6));
        FillRect(mem, &rc, bg);
        DeleteObject(bg);
        HDC pageDc = CreateCompatibleDC(dc);
        for (UINT i = 0; i < tops_.size(); ++i) {
            int top = tops_[i] - pos_;
            if (top + heights_[i] < 0 || top > rc.bottom) continue;
            HBITMAP bmp = PageBitmap(i);
            if (!bmp) continue;
            HGDIOBJ old = SelectObject(pageDc, bmp);
            BitBlt(mem, kGap, top, width_, heights_[i], pageDc, 0, 0, SRCCOPY);
            SelectObject(pageDc, old);
        }
        DeleteDC(pageDc);
        BitBlt(dc, 0, 0, rc.right, rc.bottom, mem, 0, 0, SRCCOPY);
        SelectObject(mem, oldBack);
        DeleteObject(back);
        DeleteDC(mem);
    }

    void ScrollTo(int pos) {
        RECT rc;
        GetClientRect(hwnd_, &rc);
        pos = std::max<int>(0, std::min<int>(pos, total_ - static_cast<int>(rc.bottom)));
        if (pos == pos_) return;
        pos_ = pos;
        SetScrollPos(hwnd_, SB_VERT, pos_, TRUE);
        InvalidateRect(hwnd_, nullptr, FALSE);
    }

    static LRESULT CALLBACK WndProc(HWND hwnd, UINT msg, WPARAM wp, LPARAM lp) {
        Preview* self = reinterpret_cast<Preview*>(GetWindowLongPtrW(hwnd, GWLP_USERDATA));
        if (msg == WM_NCCREATE) {
            self = static_cast<Preview*>(reinterpret_cast<CREATESTRUCTW*>(lp)->lpCreateParams);
            SetWindowLongPtrW(hwnd, GWLP_USERDATA, reinterpret_cast<LONG_PTR>(self));
        }
        if (!self || !self->renderer_) return DefWindowProcW(hwnd, msg, wp, lp);
        RECT rc;
        GetClientRect(hwnd, &rc);
        switch (msg) {
        case WM_ERASEBKGND:
            return 1;
        case WM_PAINT: {
            PAINTSTRUCT ps;
            HDC dc = BeginPaint(hwnd, &ps);
            self->Paint(dc);
            EndPaint(hwnd, &ps);
            return 0;
        }
        case WM_SIZE:
            if (self->hwnd_) self->Layout();
            return 0;
        case WM_MOUSEWHEEL:
            self->ScrollTo(self->pos_ - GET_WHEEL_DELTA_WPARAM(wp) * 60 / WHEEL_DELTA);
            return 0;
        case WM_LBUTTONDOWN:
            ::SetFocus(hwnd);
            return 0;
        case WM_KEYDOWN:
            switch (wp) {
            case VK_DOWN: self->ScrollTo(self->pos_ + 40); return 0;
            case VK_UP: self->ScrollTo(self->pos_ - 40); return 0;
            case VK_NEXT: self->ScrollTo(self->pos_ + rc.bottom - 40); return 0;
            case VK_PRIOR: self->ScrollTo(self->pos_ - rc.bottom + 40); return 0;
            case VK_HOME: self->ScrollTo(0); return 0;
            case VK_END: self->ScrollTo(self->total_); return 0;
            }
            break;
        case WM_VSCROLL: {
            SCROLLINFO si{sizeof(si), SIF_ALL};
            GetScrollInfo(hwnd, SB_VERT, &si);
            int pos = self->pos_;
            switch (LOWORD(wp)) {
            case SB_LINEUP: pos -= 40; break;
            case SB_LINEDOWN: pos += 40; break;
            case SB_PAGEUP: pos -= rc.bottom - 40; break;
            case SB_PAGEDOWN: pos += rc.bottom - 40; break;
            case SB_THUMBTRACK:
            case SB_THUMBPOSITION: pos = si.nTrackPos; break;
            case SB_TOP: pos = 0; break;
            case SB_BOTTOM: pos = self->total_; break;
            }
            self->ScrollTo(pos);
            return 0;
        }
        }
        return DefWindowProcW(hwnd, msg, wp, lp);
    }

    std::atomic<ULONG> refs_{1};
    std::vector<BYTE> data_;
    std::unique_ptr<PdfRenderer> renderer_;
    IUnknown* site_ = nullptr;
    HWND parent_ = nullptr;
    HWND hwnd_ = nullptr;
    RECT rect_{};
    int width_ = 1, total_ = 0, pos_ = 0;
    std::vector<int> tops_, heights_;
    std::map<UINT, HBITMAP> cache_;
};

HRESULT CreatePreview(REFIID riid, void** ppv) {
    auto* obj = new (std::nothrow) Preview();
    if (!obj) return E_OUTOFMEMORY;
    HRESULT hr = obj->QueryInterface(riid, ppv);
    obj->Release();
    return hr;
}

}  // namespace paperleaf
