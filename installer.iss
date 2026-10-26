#define AppVersion "1.0.0"

[Setup]
AppId={{95A40A91-DA8E-4D22-A15A-A8662C00D499}
AppName=一安藥局簡訊自動發送系統
AppVersion={#AppVersion}
AppPublisher=一安藥局
DefaultDirName={localappdata}\Programs\SMSAutoSender
DefaultGroupName=一安藥局簡訊自動發送系統
OutputDir=release
OutputBaseFilename=SMSAutoSender-Setup-{#AppVersion}
Compression=lzma2
SolidCompression=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
WizardStyle=modern
CloseApplications=yes
RestartApplications=no
UninstallDisplayIcon={app}\SMSAutoSender.exe

[Files]
Source: "dist\SMSAutoSender\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs ignoreversion

[Icons]
Name: "{group}\一安藥局簡訊自動發送系統"; Filename: "{app}\SMSAutoSender.exe"
Name: "{autodesktop}\一安藥局簡訊自動發送系統"; Filename: "{app}\SMSAutoSender.exe"; Tasks: desktopicon

[Tasks]
Name: desktopicon; Description: "建立桌面捷徑"; GroupDescription: "捷徑："; Flags: unchecked

[Run]
Filename: "{app}\SMSAutoSender.exe"; Description: "啟動簡訊自動發送系統"; Flags: nowait postinstall skipifsilent

