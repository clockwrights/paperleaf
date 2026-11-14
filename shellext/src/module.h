// Shared helpers: the DLL's reference count and module handle.
#pragma once
#include <windows.h>
#include <atomic>
#include <string>

namespace paperleaf {

extern HMODULE g_module;
extern std::atomic<long> g_objects;   // live COM objects (DllCanUnloadNow)

inline void AddObject() { ++g_objects; }
inline void ReleaseObject() { --g_objects; }

// Folder that holds PaperleafShell.dll (Paperleaf.exe is next to it).
std::wstring ModuleFolder();

}  // namespace paperleaf
