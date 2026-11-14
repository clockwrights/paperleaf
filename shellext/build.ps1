# Build PaperleafShell.dll and the signed sparse package for the Windows 11 menu.
#   powershell -ExecutionPolicy Bypass -File shellext\build.ps1
# Needs: Visual Studio 2022 (C++ desktop workload), CMake, the Windows 11 SDK
# (makeappx.exe, signtool.exe). Nothing is downloaded.
param(
    [string]$Version = "0.6.0.0",
    [string]$Publisher = "CN=Paperleaf"
)
$ErrorActionPreference = "Stop"
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$build = Join-Path $here "build"

# 1. the DLL (use the CMake that ships with Visual Studio when cmake isn't on PATH)
$cmake = (Get-Command cmake -ErrorAction SilentlyContinue).Source
if (-not $cmake) {
    $vswhere = "${env:ProgramFiles(x86)}\Microsoft Visual Studio\Installer\vswhere.exe"
    if (Test-Path $vswhere) {
        $vs = & $vswhere -latest -products * -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -property installationPath
        $cand = Join-Path "$vs" "Common7\IDE\CommonExtensions\Microsoft\CMake\CMake\bin\cmake.exe"
        if ($vs -and (Test-Path $cand)) { $cmake = $cand }
    }
}
if (-not $cmake) { throw "CMake not found: install it or Visual Studio 2022 with the C++ workload" }
& $cmake -S $here -B $build -A x64
if ($LASTEXITCODE) { throw "cmake configure failed" }
& $cmake --build $build --config Release
if ($LASTEXITCODE) { throw "cmake build failed" }
$dll = Join-Path $build "Release\PaperleafShell.dll"
if (-not (Test-Path $dll)) { throw "PaperleafShell.dll was not built" }

# 2. the sparse package (manifest + logos only)
$sdk = Get-ChildItem "${env:ProgramFiles(x86)}\Windows Kits\10\bin\10.*\x64" -Directory |
    Sort-Object Name -Descending | Select-Object -First 1
$makeappx = Join-Path $sdk.FullName "makeappx.exe"
$signtool = Join-Path $sdk.FullName "signtool.exe"
$stage = Join-Path $build "package"
Remove-Item $stage -Recurse -Force -ErrorAction SilentlyContinue
New-Item (Join-Path $stage "Assets") -ItemType Directory -Force | Out-Null
$manifest = Get-Content (Join-Path $here "package\AppxManifest.xml") -Raw
$manifest = $manifest -replace 'Version="[0-9.]+"', "Version=`"$Version`"" -replace 'Publisher="CN=[^"]*"', "Publisher=`"$Publisher`""
Set-Content (Join-Path $stage "AppxManifest.xml") $manifest -Encoding UTF8
$logo = Join-Path $here "..\resources\paperleaf.png"
foreach ($n in "Logo44.png", "Logo50.png", "Logo150.png") { Copy-Item $logo (Join-Path $stage "Assets\$n") }
$msix = Join-Path $build "PaperleafMenu.msix"
& $makeappx pack /d $stage /p $msix /nv /o
if ($LASTEXITCODE) { throw "makeappx failed" }

# 3. sign it with a local self-signed certificate (kept in your user store)
$cert = Get-ChildItem Cert:\CurrentUser\My | Where-Object { $_.Subject -eq $Publisher } | Select-Object -First 1
if (-not $cert) {
    $cert = New-SelfSignedCertificate -Type Custom -Subject $Publisher -KeyUsage DigitalSignature `
        -CertStoreLocation Cert:\CurrentUser\My -TextExtension @("2.5.29.37={text}1.3.6.1.5.5.7.3.3",
        "2.5.29.19={text}") -FriendlyName "Paperleaf package signing"
}
Export-Certificate -Cert $cert -FilePath (Join-Path $build "PaperleafMenu.cer") | Out-Null
& $signtool sign /fd SHA256 /sha1 $cert.Thumbprint $msix
if ($LASTEXITCODE) { throw "signtool failed" }
Write-Host "Built $dll and $msix"
