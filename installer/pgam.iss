#define MyAppName "PostGraduate Admission Monitor"
#define MyAppVersion "0.1.0"
#define MyAppPublisher "Personal Project"
#define MyAppExeName "PostGraduateAdmissionMonitor.exe"

[Setup]
AppId={{8C6CFF91-55AE-4F16-BD5D-93D99F2742B3}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
UninstallDisplayIcon={app}\{#MyAppExeName}
UninstallDisplayName={#MyAppName} {#MyAppVersion}
OutputDir=..\dist\installer
OutputBaseFilename=PGAM-0.1.0-x64
SetupIconFile=assets\app.ico
SetupLogging=yes
CloseApplications=yes
RestartApplications=no
DisableProgramGroupPage=yes
MinVersion=10.0
Compression=lzma2/max
SolidCompression=yes
ArchitecturesInstallIn64BitMode=x64
WizardStyle=modern
PrivilegesRequired=lowest

[Languages]
Name: "chinesesimplified"; MessagesFile: "assets\ChineseSimplified.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"
Name: "autostart"; Description: "开机自动启动研究生招生信息监视系统"; GroupDescription: "启动选项："

[Files]
Source: "..\dist\PGAM\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Registry]
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: string; ValueName: "PostGraduateAdmissionMonitor"; ValueData: """{app}\{#MyAppExeName}"" --hidden"; Flags: uninsdeletevalue; Tasks: autostart

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "{cm:LaunchProgram,{#MyAppName}}"; Flags: nowait postinstall skipifsilent


