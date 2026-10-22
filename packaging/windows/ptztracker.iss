; Установщик PTZ Tracker для Windows (Inno Setup 6).
; Сборка: ISCC.exe /DAppVersion=0.3.0 packaging\windows\ptztracker.iss  (после pyinstaller)
; Обновление: новый установщик ставится поверх старого с тем же AppId, закрывает запущенную программу;
; настройки, калибровка и пароли лежат в %APPDATA% и диспетчере учётных данных и не трогаются.

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif
#define AppName "PTZ Tracker"
#define AppExe "PTZTracker.exe"
#define DiagExe "PTZTracker-diag.exe"
#define Root "..\.."

[Setup]
; AppId не менять: по нему установщик находит прежнюю версию и обновляет её на месте
AppId={{7A84C89E-6E57-4144-91A7-C2575B8D5806}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher=funpin
AppPublisherURL=https://github.com/funpin/ptz-tracker
AppSupportURL=https://github.com/funpin/ptz-tracker/issues
AppUpdatesURL=https://github.com/funpin/ptz-tracker/releases
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
; установка для текущего пользователя — без запроса прав администратора
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir={#Root}\dist
OutputBaseFilename=PTZTracker-{#AppVersion}-windows-x64-setup
SetupIconFile={#Root}\packaging\icons\ptztracker.ico
UninstallDisplayIcon={app}\{#AppExe}
LicenseFile={#Root}\LICENSE
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
CloseApplications=yes
RestartApplications=no

[Languages]
Name: "russian"; MessagesFile: "compiler:Languages\Russian.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"

[InstallDelete]
; старые библиотеки прежней версии — убрать, чтобы не смешались с новыми
Type: filesandordirs; Name: "{app}\_internal"

[Files]
Source: "{#Root}\dist\PTZTracker\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExe}"
Name: "{group}\{#AppName} — диагностика камеры"; Filename: "{app}\{#DiagExe}"
Name: "{group}\{cm:UninstallProgram,{#AppName}}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExe}"; Description: "{cm:LaunchProgram,{#AppName}}"; Flags: nowait postinstall skipifsilent
