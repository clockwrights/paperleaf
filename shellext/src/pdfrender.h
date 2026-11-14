// Renders PDF pages with Windows' own PDF engine (Windows.Data.Pdf). Offline,
// no extra libraries. WinRT calls are made on a private multi-threaded worker,
// so the shell's single-threaded apartment is never blocked on .get().
#pragma once
#include <windows.h>
#include <objidl.h>

#include <condition_variable>
#include <functional>
#include <mutex>
#include <queue>
#include <thread>
#include <vector>

namespace paperleaf {

// Read a whole IStream into memory (on the caller's thread: shell streams are
// bound to the thread that received them).
HRESULT ReadAll(IStream* stream, std::vector<BYTE>& out, ULONGLONG limit = 512ull * 1024 * 1024);

class PdfRenderer {
public:
    PdfRenderer();
    ~PdfRenderer();
    PdfRenderer(const PdfRenderer&) = delete;
    PdfRenderer& operator=(const PdfRenderer&) = delete;

    // Load a PDF from bytes. Fails for damaged or password-protected files.
    HRESULT Open(const std::vector<BYTE>& data);
    UINT PageCount() const { return static_cast<UINT>(sizes_.size()); }
    // Page size in points, as displayed (rotation applied).
    SIZE PageSize(UINT index) const { return sizes_.at(index); }
    // Render one page to a 32-bit top-down DIB of the given size (white paper).
    HRESULT Render(UINT index, UINT width, UINT height, HBITMAP* out);

private:
    void Run(std::function<void()> job);   // run on the worker and wait
    void Loop();

    std::thread worker_;
    std::mutex lock_;
    std::condition_variable wake_;
    std::queue<std::function<void()>> jobs_;
    bool quit_ = false;
    void* doc_ = nullptr;                   // winrt::Windows::Data::Pdf::PdfDocument (worker-owned)
    std::vector<SIZE> sizes_;
};

}  // namespace paperleaf
