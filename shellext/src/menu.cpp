// Windows 11 top-level right-click menu for PDF files: a "Paperleaf" entry with
// the same tools as the classic menu. Each command starts Paperleaf.exe (next to
// this DLL) once, with every selected file:  Paperleaf.exe --tool combine a.pdf b.pdf
#include <windows.h>
#include <shlwapi.h>
#include <shobjidl.h>

#include <atomic>
#include <new>
#include <string>
#include <vector>

#include "module.h"

namespace paperleaf {

namespace {

struct Tool {
    const wchar_t* id;      // "" = open the files
    const wchar_t* title;
};

// keep in step with TOOL_VERBS in paperleaf/winshell/registry.py
const Tool kTools[] = {
    {L"", L"Open with Paperleaf"},
    {L"combine", L"Combine files"},
    {L"split", L"Split\x2026"},
    {L"extract", L"Extract pages\x2026"},
    {L"rotate", L"Rotate pages\x2026"},
    {L"compress", L"Reduce file size\x2026"},
    {L"word", L"Convert to Word"},
    {L"images", L"Convert to images\x2026"},
    {L"protect", L"Protect with password\x2026"},
};

std::wstring Quote(const std::wstring& s) { return L"\"" + s + L"\""; }

std::wstring PaperleafExe() { return ModuleFolder() + L"\\Paperleaf.exe"; }

HRESULT SelectedPaths(IShellItemArray* items, std::vector<std::wstring>& out) {
    if (!items) return E_INVALIDARG;
    DWORD n = 0;
    HRESULT hr = items->GetCount(&n);
    if (FAILED(hr)) return hr;
    for (DWORD i = 0; i < n; ++i) {
        IShellItem* item = nullptr;
        if (FAILED(items->GetItemAt(i, &item))) continue;
        PWSTR path = nullptr;
        if (SUCCEEDED(item->GetDisplayName(SIGDN_FILESYSPATH, &path))) {
            out.emplace_back(path);
            CoTaskMemFree(path);
        }
        item->Release();
    }
    return out.empty() ? E_FAIL : S_OK;
}

HRESULT Launch(const wchar_t* tool, const std::vector<std::wstring>& paths) {
    std::wstring cmd = Quote(PaperleafExe());
    if (*tool) cmd += std::wstring(L" --tool ") + tool;
    for (const auto& p : paths) cmd += L" " + Quote(p);
    STARTUPINFOW si{sizeof(si)};
    PROCESS_INFORMATION pi{};
    std::vector<wchar_t> buf(cmd.begin(), cmd.end());
    buf.push_back(0);
    if (!CreateProcessW(nullptr, buf.data(), nullptr, nullptr, FALSE, 0, nullptr, nullptr, &si, &pi))
        return HRESULT_FROM_WIN32(GetLastError());
    AllowSetForegroundWindow(pi.dwProcessId);
    CloseHandle(pi.hThread);
    CloseHandle(pi.hProcess);
    return S_OK;
}

}  // namespace

// One menu entry. The root (index -1) has the tools as sub-commands.
class Command final : public IExplorerCommand, public IEnumExplorerCommand {
public:
    explicit Command(int index) : index_(index) { AddObject(); }

    // IUnknown
    IFACEMETHODIMP QueryInterface(REFIID riid, void** ppv) override {
        static const QITAB qit[] = {
            QITABENT(Command, IExplorerCommand),
            QITABENT(Command, IEnumExplorerCommand),
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

    // IExplorerCommand
    IFACEMETHODIMP GetTitle(IShellItemArray*, LPWSTR* name) override {
        return SHStrDupW(index_ < 0 ? L"Paperleaf" : kTools[index_].title, name);
    }
    IFACEMETHODIMP GetIcon(IShellItemArray*, LPWSTR* icon) override {
        return SHStrDupW((PaperleafExe() + L",0").c_str(), icon);
    }
    IFACEMETHODIMP GetToolTip(IShellItemArray*, LPWSTR* tip) override {
        *tip = nullptr;
        return E_NOTIMPL;
    }
    IFACEMETHODIMP GetCanonicalName(GUID* guid) override {
        *guid = GUID_NULL;
        return S_OK;
    }
    IFACEMETHODIMP GetState(IShellItemArray* items, BOOL, EXPCMDSTATE* state) override {
        *state = ECS_ENABLED;
        if (index_ >= 0 && wcscmp(kTools[index_].id, L"combine") == 0 && items) {
            DWORD n = 0;
            if (SUCCEEDED(items->GetCount(&n)) && n < 2) *state = ECS_HIDDEN;   // combine needs 2+ files
        }
        return S_OK;
    }
    IFACEMETHODIMP Invoke(IShellItemArray* items, IBindCtx*) override {
        if (index_ < 0) return E_NOTIMPL;
        std::vector<std::wstring> paths;
        HRESULT hr = SelectedPaths(items, paths);
        if (FAILED(hr)) return hr;
        return Launch(kTools[index_].id, paths);
    }
    IFACEMETHODIMP GetFlags(EXPCMDFLAGS* flags) override {
        *flags = index_ < 0 ? ECF_HASSUBCOMMANDS : ECF_DEFAULT;
        return S_OK;
    }
    IFACEMETHODIMP EnumSubCommands(IEnumExplorerCommand** out) override {
        if (index_ >= 0) {
            *out = nullptr;
            return E_NOTIMPL;
        }
        auto* e = new (std::nothrow) Command(-1);
        if (!e) return E_OUTOFMEMORY;
        e->cursor_ = 0;
        *out = e;
        return S_OK;
    }

    // IEnumExplorerCommand (only used on the root's enumerator)
    IFACEMETHODIMP Next(ULONG count, IExplorerCommand** out, ULONG* fetched) override {
        ULONG got = 0;
        while (got < count && cursor_ < static_cast<int>(ARRAYSIZE(kTools))) {
            auto* c = new (std::nothrow) Command(cursor_++);
            if (!c) break;
            out[got++] = c;
        }
        if (fetched) *fetched = got;
        return got == count ? S_OK : S_FALSE;
    }
    IFACEMETHODIMP Skip(ULONG count) override {
        cursor_ += static_cast<int>(count);
        return cursor_ <= static_cast<int>(ARRAYSIZE(kTools)) ? S_OK : S_FALSE;
    }
    IFACEMETHODIMP Reset() override {
        cursor_ = 0;
        return S_OK;
    }
    IFACEMETHODIMP Clone(IEnumExplorerCommand** out) override {
        auto* c = new (std::nothrow) Command(-1);
        if (!c) return E_OUTOFMEMORY;
        c->cursor_ = cursor_;
        *out = c;
        return S_OK;
    }

private:
    ~Command() { ReleaseObject(); }
    std::atomic<ULONG> refs_{1};
    int index_;
    int cursor_ = 0;
};

HRESULT CreateMenu(REFIID riid, void** ppv) {
    auto* obj = new (std::nothrow) Command(-1);
    if (!obj) return E_OUTOFMEMORY;
    HRESULT hr = obj->QueryInterface(riid, ppv);
    obj->Release();
    return hr;
}

}  // namespace paperleaf
