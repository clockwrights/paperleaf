// DLL entry points and the class factory for PaperleafShell.dll. Registration is
// done by Paperleaf itself (Paperleaf.exe --register, see paperleaf/winshell/registry.py)
// for the thumbnails and the preview, and by the sparse MSIX package
// (package/AppxManifest.xml) for the Windows 11 menu.
#include <windows.h>
#include <shlwapi.h>

#include <new>

#include "guids.h"
#include "module.h"

namespace paperleaf {

HMODULE g_module = nullptr;
std::atomic<long> g_objects{0};
static std::atomic<long> g_locks{0};

HRESULT CreateThumbnail(REFIID riid, void** ppv);
HRESULT CreatePreview(REFIID riid, void** ppv);
HRESULT CreateMenu(REFIID riid, void** ppv);

std::wstring ModuleFolder() {
    wchar_t path[MAX_PATH * 2] = {};
    GetModuleFileNameW(g_module, path, ARRAYSIZE(path));
    PathRemoveFileSpecW(path);
    return path;
}

using Creator = HRESULT (*)(REFIID, void**);

class Factory final : public IClassFactory {
public:
    explicit Factory(Creator create) : create_(create) { AddObject(); }

    IFACEMETHODIMP QueryInterface(REFIID riid, void** ppv) override {
        static const QITAB qit[] = {QITABENT(Factory, IClassFactory), {nullptr, 0}};
        return QISearch(this, qit, riid, ppv);
    }
    IFACEMETHODIMP_(ULONG) AddRef() override { return ++refs_; }
    IFACEMETHODIMP_(ULONG) Release() override {
        ULONG n = --refs_;
        if (n == 0) delete this;
        return n;
    }
    IFACEMETHODIMP CreateInstance(IUnknown* outer, REFIID riid, void** ppv) override {
        *ppv = nullptr;
        if (outer) return CLASS_E_NOAGGREGATION;
        return create_(riid, ppv);
    }
    IFACEMETHODIMP LockServer(BOOL lock) override {
        lock ? ++g_locks : --g_locks;
        return S_OK;
    }

private:
    ~Factory() { ReleaseObject(); }
    std::atomic<ULONG> refs_{1};
    Creator create_;
};

}  // namespace paperleaf

using namespace paperleaf;

BOOL APIENTRY DllMain(HMODULE module, DWORD reason, LPVOID) {
    if (reason == DLL_PROCESS_ATTACH) {
        g_module = module;
        DisableThreadLibraryCalls(module);
    }
    return TRUE;
}

STDAPI DllGetClassObject(REFCLSID clsid, REFIID riid, void** ppv) {
    *ppv = nullptr;
    Creator create = nullptr;
    if (IsEqualCLSID(clsid, CLSID_PaperleafThumbnail)) create = CreateThumbnail;
    else if (IsEqualCLSID(clsid, CLSID_PaperleafPreview)) create = CreatePreview;
    else if (IsEqualCLSID(clsid, CLSID_PaperleafMenu)) create = CreateMenu;
    else return CLASS_E_CLASSNOTAVAILABLE;
    auto* f = new (std::nothrow) Factory(create);
    if (!f) return E_OUTOFMEMORY;
    HRESULT hr = f->QueryInterface(riid, ppv);
    f->Release();
    return hr;
}

STDAPI DllCanUnloadNow() { return (g_objects == 0 && g_locks == 0) ? S_OK : S_FALSE; }
