; Paperleaf installer (per-user, no admin rights needed).
; Build:  "%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe" installer\paperleaf.iss

#define AppName "Paperleaf"
#define AppVersion "0.6.0"
#define AppExe "Paperleaf.exe"
; the Windows 11 menu package is included only when shellext\build.ps1 has built it
#define HaveMenu FileExists(AddBackslash(SourcePath) + "..\shellext\build\PaperleafMenu.msix")

[Setup]
AppId={{FCD68866-E340-4CB1-B4F9-3A561AF6DF5F}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher={#AppName}
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir=Output
OutputBaseFilename=PaperleafSetup-{#AppVersion}
LicenseFile=..\LICENSE
SetupIconFile=..\resources\paperleaf.ico
UninstallDisplayIcon={app}\{#AppExe}
UninstallDisplayName={#AppName} PDF Reader
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
CloseApplications=yes
RestartApplications=no
ChangesAssociations=yes

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop shortcut"; GroupDescription: "Shortcuts:"; Flags: unchecked
#if HaveMenu
Name: "win11menu"; Description: "Add Paperleaf to the Windows &11 right-click menu (asks for administrator permission once)"; GroupDescription: "File Explorer:"; Flags: unchecked; Check: IsWindows11
#endif

[Files]
Source: "..\dist\Paperleaf\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
; Explorer thumbnails, preview pane and the Windows 11 menu (built by shellext\build.ps1)
Source: "..\shellext\build\Release\PaperleafShell.dll"; DestDir: "{app}"; Flags: ignoreversion skipifsourcedoesntexist
Source: "..\shellext\build\PaperleafMenu.msix"; DestDir: "{app}"; Flags: ignoreversion skipifsourcedoesntexist
Source: "..\shellext\build\PaperleafMenu.cer"; DestDir: "{app}"; Flags: ignoreversion skipifsourcedoesntexist
Source: "..\shellext\install_menu.ps1"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{autoprograms}\{#AppName}"; Filename: "{app}\{#AppExe}"; Comment: "Fast, private PDF reader"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; Tasks: desktopicon

[Run]
; add Explorer integration: Open with, right-click menu, Default apps listing
Filename: "{app}\{#AppExe}"; Parameters: "--register"; StatusMsg: "Adding Paperleaf to Windows Explorer..."; Flags: runhidden waituntilterminated
#if HaveMenu
Filename: "powershell.exe"; Parameters: "-NoProfile -ExecutionPolicy Bypass -File ""{app}\install_menu.ps1"" -AppDir ""{app}"""; StatusMsg: "Adding Paperleaf to the Windows 11 right-click menu..."; Tasks: win11menu; Flags: runhidden waituntilterminated
#endif
Filename: "ms-settings:defaultapps?registeredAppUser={#AppName}"; Description: "Make Paperleaf my default PDF app (opens Windows Settings)"; Flags: shellexec postinstall skipifsilent nowait
Filename: "{app}\{#AppExe}"; Description: "Start Paperleaf"; Flags: postinstall skipifsilent nowait

[UninstallRun]
Filename: "{app}\{#AppExe}"; Parameters: "--unregister"; RunOnceId: "UnregisterPaperleaf"; Flags: runhidden waituntilterminated
Filename: "powershell.exe"; Parameters: "-NoProfile -ExecutionPolicy Bypass -File ""{app}\install_menu.ps1"" -Remove"; RunOnceId: "RemovePaperleafMenu"; Flags: runhidden waituntilterminated

[Code]
function IsWindows11: Boolean;
begin
  Result := GetWindowsVersion >= $0A0055F0;  { 10.0.22000 }
end;
