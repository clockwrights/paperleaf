# Add (or remove) Paperleaf in the Windows 11 top-level right-click menu.
# Run by the installer when "Add Paperleaf to the Windows 11 right-click menu" is
# ticked. Trusting the package certificate needs administrator permission once.
#   install_menu.ps1 -AppDir "C:\Users\me\AppData\Local\Programs\Paperleaf"
#   install_menu.ps1 -Remove
param(
    [string]$AppDir = (Split-Path -Parent $MyInvocation.MyCommand.Path),
    [switch]$Remove
)
$ErrorActionPreference = "Stop"
$name = "Paperleaf.ExplorerMenu"

Get-AppxPackage -Name $name | Remove-AppxPackage -ErrorAction SilentlyContinue
if ($Remove) { exit 0 }

$msix = Join-Path $AppDir "PaperleafMenu.msix"
$cer = Join-Path $AppDir "PaperleafMenu.cer"
if (-not (Test-Path $msix)) { throw "PaperleafMenu.msix is missing from $AppDir" }

# the package is signed with Paperleaf's own certificate: trust it for packages
$trusted = Get-ChildItem Cert:\LocalMachine\TrustedPeople |
    Where-Object { $_.Thumbprint -eq (Get-PfxCertificate $cer).Thumbprint }
if (-not $trusted) {
    $p = Start-Process powershell -Verb RunAs -Wait -PassThru -ArgumentList @(
        "-NoProfile", "-Command", "Import-Certificate -FilePath '$cer' -CertStoreLocation Cert:\LocalMachine\TrustedPeople")
    if ($p.ExitCode) { throw "The certificate was not trusted." }
}
Add-AppxPackage -Path $msix -ExternalLocation $AppDir
Write-Host "Paperleaf is now in the Windows 11 right-click menu."
