#ifndef MyAppVersion
  #define MyAppVersion "1.16.7"
#endif
#define MyAppName "TokenMeter"
#define MyAppExeName "TokenMeter.exe"
; 记录构建时的主程序摘要，安装文件被延迟替换时不能误记为已完成更新。
#define MyAppExeSHA256 GetSHA256OfFile(AddBackslash(SourcePath) + "..\..\dist\TokenMeter\TokenMeter.exe")

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
; 排除规则必须从根目录匹配，否则 pet\* 也会误排除 _internal\assets\pet 内的角色缩略图。
Source: "..\..\dist\TokenMeter\*"; DestDir: "{app}"; Excludes: "\data\*,\pet\*,\_internal\pet\*,\_internal\assets\pets\*"; Flags: ignoreversion recursesubdirs createallsubdirs

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
Type: files; Name: "{app}\.tokenmeter-install-receipt"

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "{cm:LaunchProgram,{#StringChange(MyAppName, '&', '&&')}}"; Flags: nowait postinstall skipifsilent; Check: not IsUpdateMode
; 自动更新成功后只由安装器重启一次，避免主程序与外部更新进程重复启动。
Filename: "{app}\{#MyAppExeName}"; Flags: nowait skipifdoesntexist; Check: IsUpdateMode

[Code]
procedure CurStepChanged(CurStep: TSetupStep);
var
  ReceiptPath: String;
  ReceiptText: AnsiString;
begin
  ReceiptPath := ExpandConstant('{app}\.tokenmeter-install-receipt');
  if CurStep = ssInstall then
    DeleteFile(ReceiptPath)
  else if CurStep = ssPostInstall then
  begin
    try
      // 只有安装后的主程序与本安装包一致时才写入记录，失败时保留旧版检查方式。
      if CompareText(GetSHA256OfFile(ExpandConstant('{app}\{#MyAppExeName}')), '{#MyAppExeSHA256}') = 0 then
      begin
        ReceiptText := AnsiString('{#MyAppVersion}' + #13#10 +
          GetSHA256OfFile(ExpandConstant('{srcexe}')) + #13#10 + '{#MyAppExeSHA256}' + #13#10);
        if not SaveStringToFile(ReceiptPath, ReceiptText, False) then
        begin
          DeleteFile(ReceiptPath);
          Log('Could not write update installation receipt');
        end;
      end;
    except
      DeleteFile(ReceiptPath);
      Log('Could not record update installation receipt: ' + GetExceptionMessage);
    end;
  end;
end;

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
