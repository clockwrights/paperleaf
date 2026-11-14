// Class IDs of the COM objects in PaperleafShell.dll. Keep in sync with
// paperleaf/winshell/registry.py and package/AppxManifest.xml.
#pragma once
#include <guiddef.h>

// {BDA307E0-58BF-4583-B73E-DA8130B4AE52}  Explorer thumbnails (IThumbnailProvider)
inline constexpr GUID CLSID_PaperleafThumbnail =
    {0xbda307e0, 0x58bf, 0x4583, {0xb7, 0x3e, 0xda, 0x81, 0x30, 0xb4, 0xae, 0x52}};

// {C72D2B1B-EE52-4E4E-BF99-08C7ACCE5C6D}  Preview pane (IPreviewHandler)
inline constexpr GUID CLSID_PaperleafPreview =
    {0xc72d2b1b, 0xee52, 0x4e4e, {0xbf, 0x99, 0x08, 0xc7, 0xac, 0xce, 0x5c, 0x6d}};

// {6067870F-1205-4F4C-9373-75BE95DBD111}  Windows 11 right-click menu (IExplorerCommand)
inline constexpr GUID CLSID_PaperleafMenu =
    {0x6067870f, 0x1205, 0x4f4c, {0x93, 0x73, 0x75, 0xbe, 0x95, 0xdb, 0xd1, 0x11}};
