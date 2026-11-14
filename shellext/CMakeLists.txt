# PaperleafShell.dll: Explorer thumbnails, preview pane and the Windows 11 menu for PDFs.
# Needs Visual Studio 2022 (C++ desktop workload) and the Windows 11 SDK
# (C++/WinRT headers ship with it). Build with build.ps1.
cmake_minimum_required(VERSION 3.20)
project(PaperleafShell LANGUAGES CXX)

if(NOT MSVC)
    message(FATAL_ERROR "PaperleafShell is a Windows shell extension; build it with MSVC.")
endif()

set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_MSVC_RUNTIME_LIBRARY "MultiThreaded")   # static CRT: no VC++ redistributable needed

add_library(PaperleafShell SHARED
    src/dll.cpp
    src/pdfrender.cpp
    src/thumbnail.cpp
    src/preview.cpp
    src/menu.cpp
    src/PaperleafShell.def
)
target_compile_definitions(PaperleafShell PRIVATE UNICODE _UNICODE NOMINMAX WIN32_LEAN_AND_MEAN _WIN32_WINNT=0x0A00)
target_compile_options(PaperleafShell PRIVATE /W4 /permissive- /EHsc /utf-8)
target_link_libraries(PaperleafShell PRIVATE shlwapi shell32 ole32 oleaut32 uuid windowscodecs shcore windowsapp)
