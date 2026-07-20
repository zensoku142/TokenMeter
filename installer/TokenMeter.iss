#ifndef MyAppVersion
  #define MyAppVersion "1.10.4"
#endif
#define MyAppName "TokenMeter"
#define MyAppExeName "TokenMeter.exe"

[Setup]
AppId={{6CF354B5-80AE-48BF-AFC5-890BDA5D8862}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher=zensoku142
AppPublisherURL=https://github.com/zensoku142/TokenMeter
AppSupportURL=https://github.com/zensoku142/TokenMeter/issues
AppUpdatesURL=https://github.com/zensoku142/TokenMeter/releases
DefaultDirName={localappdata}\Programs\TokenMeter
DefaultGroupName=TokenMeter
PrivilegesRequired=lowest
UsePreviousAppDir=yes
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir=..\dist-installer
OutputBaseFilename=TokenMeter-Setup-v{#MyAppVersion}-x64
SetupIconFile=..\assets\TokenMeter.ico
UninstallDisplayIcon={app}\{#MyAppExeName}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
CloseApplications=yes
RestartApplications=no
DisableProgramGroupPage=yes

[Languages]
Name: "chinesesimplified"; MessagesFile: "languages\ChineseSimplified.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"
Name: "japanese"; MessagesFile: "compiler:Languages\Japanese.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: checkedonce

[Files]
; The build output never contains user data; the exclusion adds defense in depth.
Source: "..\dist\TokenMeter\*"; DestDir: "{app}"; Excludes: "data\*"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{userdesktop}\TokenMeter"; Filename: "{app}\{#MyAppExeName}"; WorkingDir: "{app}"; IconFilename: "{app}\{#MyAppExeName}"; Tasks: desktopicon
Name: "{group}\TokenMeter"; Filename: "{app}\{#MyAppExeName}"; WorkingDir: "{app}"
Name: "{group}\卸载 TokenMeter"; Filename: "{uninstallexe}"

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "{cm:LaunchProgram,{#StringChange(MyAppName, '&', '&&')}}"; Flags: nowait postinstall skipifsilent; Check: not IsUpdateMode
; 自动更新成功后只由安装器重启一次，避免主程序与外部更新进程重复启动。
Filename: "{app}\{#MyAppExeName}"; Flags: nowait skipifdoesntexist; Check: IsUpdateMode

[Code]
function IsUpdateMode: Boolean;
var
  I: Integer;
begin
  Result := False;
  for I := 1 to ParamCount do
  begin
    if CompareText(ParamStr(I), '/TOKENMETERUPDATE') = 0 then
    begin
      Result := True;
      Exit;
    end;
  end;
end;
