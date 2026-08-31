#ifndef MyAppVersion
  #define MyAppVersion "1.14.0"
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
OutputDir=..\..\dist-installer
OutputBaseFilename=TokenMeter-Setup-v{#MyAppVersion}-x64
SetupIconFile=..\..\assets\TokenMeter.ico
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
; 桌宠只通过独立扩展附件安装；即使构建目录残留旧 pet，也不能带入主安装包。
Source: "..\..\dist\TokenMeter\*"; DestDir: "{app}"; Excludes: "data\*,pet\*,_internal\pet\*,_internal\assets\pets\*"; Flags: ignoreversion recursesubdirs createallsubdirs

[InstallDelete]
; 旧试用包误带入了第三方 ICU。普通覆盖安装不会删除遗留 DLL，必须清理这些精确路径，不能触碰 data。
Type: files; Name: "{app}\_internal\icuuc.dll"
Type: files; Name: "{app}\_internal\icuin.dll"
Type: files; Name: "{app}\_internal\icu.dll"
Type: files; Name: "{app}\_internal\icudt78.dll"

[Icons]
Name: "{userdesktop}\TokenMeter"; Filename: "{app}\{#MyAppExeName}"; WorkingDir: "{app}"; IconFilename: "{app}\{#MyAppExeName}"; Tasks: desktopicon
Name: "{group}\TokenMeter"; Filename: "{app}\{#MyAppExeName}"; WorkingDir: "{app}"
Name: "{group}\卸载 TokenMeter"; Filename: "{uninstallexe}"

[UninstallDelete]
Type: files; Name: "{userstartup}\TokenMeter.lnk"

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
