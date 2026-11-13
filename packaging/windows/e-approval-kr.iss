; e-approval-kr Windows installer (Inno Setup 6).
;
; Build (from the repository root, after `python packaging\build.py` and fetching WinSW):
;   iscc /DAppVersion=0.1.0 packaging\windows\e-approval-kr.iss
; -> dist\e-approval-kr-setup-<ver>-win-x64.exe
;
; What it does
;   - program        : C:\Program Files\e-approval-kr\  (per-machine, admin)
;   - data           : %ProgramData%\e-approval-kr\ (ACL: SYSTEM, Administrators, LOCAL SERVICE only)
;   - Windows service: "e-approval-kr" via WinSW (e-approval-kr-service.exe + generated .xml),
;                      runs as NT AUTHORITY\LocalService, starts automatically,
;                      `e-approval-kr.exe serve` with GYEOLJAE_DATA set (host/port from server.json,
;                      default 127.0.0.1:8130)
;   - optional task  : Windows Firewall rule for tcp/8130 from the local subnet only (off by default)
;   - Start menu     : 서버 열기 / 관리 도구 / 설명서 / 제거
;   - uninstall      : removes program + service + firewall rule; keeps data unless the user answers
;                      "yes" to 데이터도 삭제 (silent: pass /PURGEDATA to delete data)

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif
#ifndef BundleDir
  #define BundleDir "..\..\dist\e-approval-kr"
#endif
#ifndef WinSWDir
  #define WinSWDir "..\..\build\winsw"
#endif
#ifndef OutDir
  #define OutDir "..\..\dist"
#endif
#define AppName "e-approval-kr"
#define SvcName "e-approval-kr"
#define Port "8130"

[Setup]
AppId={{6A7CAB46-9ACB-4A0F-B755-81BF75976B75}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher=e-approval-kr contributors
AppPublisherURL=https://github.com/gsungmoon-sketch/e-approval-kr
AppSupportURL=https://github.com/gsungmoon-sketch/e-approval-kr/issues
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
DisableDirPage=auto
PrivilegesRequired=admin
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
OutputDir={#OutDir}
OutputBaseFilename=e-approval-kr-setup-{#AppVersion}-win-x64
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
LicenseFile=..\..\LICENSE
UninstallDisplayIcon={app}\e-approval-kr.exe
UninstallDisplayName={#AppName} {#AppVersion}
CloseApplications=no
SetupLogging=yes

[Languages]
#if FileExists(AddBackslash(CompilerPath) + "Languages\Korean.isl")
Name: "korean"; MessagesFile: "compiler:Languages\Korean.isl"
#endif
Name: "english"; MessagesFile: "compiler:Default.isl"

[CustomMessages]
TaskFirewall=사내 LAN 접속 허용 방화벽 규칙 추가 (같은 서브넷만, TCP {#Port}) / Allow LAN access in Windows Firewall (local subnet only)
TaskFirewallGroup=네트워크 / Network:
OpenSetup=설정 페이지 열기 (http://127.0.0.1:{#Port}/setup) / Open the setup page
PurgeAsk=데이터도 삭제하시겠습니까?%n%n%1%n%n문서·첨부·감사로그가 모두 지워지며 되돌릴 수 없습니다. [아니요]를 누르면 데이터는 남고, 다시 설치하면 그대로 이어서 씁니다.%n%nAlso delete all data (documents, attachments, audit log)? This cannot be undone. Choose No to keep it.
SvcFail=서비스 시작에 실패했습니다. 로그를 확인하세요: %1%n%nThe e-approval-kr service failed to start. See the logs in: %1

[Tasks]
Name: "firewall"; Description: "{cm:TaskFirewall}"; GroupDescription: "{cm:TaskFirewallGroup}"; Flags: unchecked

[Dirs]
Name: "{commonappdata}\{#AppName}"; Flags: uninsneveruninstall
Name: "{commonappdata}\{#AppName}\logs"; Flags: uninsneveruninstall

[Files]
Source: "{#BundleDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "{#WinSWDir}\WinSW.NET461.exe"; DestDir: "{app}"; DestName: "e-approval-kr-service.exe"; Flags: ignoreversion
Source: "{#WinSWDir}\LICENSE.txt"; DestDir: "{app}"; DestName: "WinSW-LICENSE.txt"; Flags: ignoreversion
Source: "admin-console.cmd"; DestDir: "{app}"; Flags: ignoreversion

[INI]
Filename: "{group}\서버 열기 (Open server).url"; Section: "InternetShortcut"; Key: "URL"; String: "http://127.0.0.1:{#Port}/"

[Icons]
Name: "{group}\관리 도구 (Admin console)"; Filename: "{app}\admin-console.cmd"; WorkingDir: "{app}"; IconFilename: "{app}\e-approval-kr.exe"
Name: "{group}\설명서 (Manual)"; Filename: "{app}\manual\index.html"; Check: ManualExists
Name: "{group}\{#AppName} 제거 (Uninstall)"; Filename: "{uninstallexe}"

[Run]
Filename: "http://127.0.0.1:{#Port}/setup"; Description: "{cm:OpenSetup}"; Flags: shellexec postinstall nowait skipifsilent

[UninstallDelete]
Type: files; Name: "{group}\서버 열기 (Open server).url"
Type: files; Name: "{app}\e-approval-kr-service.xml"
Type: files; Name: "{app}\*.log"
Type: dirifempty; Name: "{app}"

[Code]
var
  PurgeData: Boolean;

function ManualExists: Boolean;
begin
  Result := FileExists(ExpandConstant('{app}\manual\index.html'));
end;

function RunCmd(const FileName, Params: String): Integer;
var
  Code: Integer;
begin
  if not Exec(FileName, Params, '', SW_HIDE, ewWaitUntilTerminated, Code) then
    Code := -1;
  Log(Format('exec: %s %s -> %d', [FileName, Params, Code]));
  Result := Code;
end;

function Sc(const Params: String): Integer;
begin
  Result := RunCmd(ExpandConstant('{sys}\sc.exe'), Params);
end;

function ServiceExists: Boolean;
begin
  Result := Sc('query {#SvcName}') = 0;
end;

procedure StopService;
begin
  if ServiceExists then
    RunCmd(ExpandConstant('{sys}\net.exe'), 'stop {#SvcName}');  { net stop waits until stopped }
end;

function XmlEsc(const S: String): String;
begin
  Result := S;
  StringChangeEx(Result, '&', '&amp;', True);
  StringChangeEx(Result, '<', '&lt;', True);
  StringChangeEx(Result, '>', '&gt;', True);
  StringChangeEx(Result, '"', '&quot;', True);
end;

procedure WriteServiceXml;
var
  Data, Xml: String;
begin
  Data := ExpandConstant('{commonappdata}\{#AppName}');
  Xml :=
    '<service>' + #13#10 +
    '  <id>{#SvcName}</id>' + #13#10 +
    '  <name>e-approval-kr</name>' + #13#10 +
    '  <description>e-approval-kr electronic approval server (data: ' + XmlEsc(Data) + ')</description>' + #13#10 +
    '  <executable>' + XmlEsc(ExpandConstant('{app}\e-approval-kr.exe')) + '</executable>' + #13#10 +
    '  <arguments>serve</arguments>' + #13#10 +
    '  <workingdirectory>' + XmlEsc(Data) + '</workingdirectory>' + #13#10 +
    '  <env name="GYEOLJAE_DATA" value="' + XmlEsc(Data) + '"/>' + #13#10 +
    '  <env name="PYTHONIOENCODING" value="utf-8"/>' + #13#10 +
    '  <startmode>Automatic</startmode>' + #13#10 +
    '  <stoptimeout>15 sec</stoptimeout>' + #13#10 +
    '  <onfailure action="restart" delay="5 sec"/>' + #13#10 +
    '  <onfailure action="restart" delay="30 sec"/>' + #13#10 +
    '  <resetfailure>1 hour</resetfailure>' + #13#10 +
    '  <logpath>' + XmlEsc(Data + '\logs') + '</logpath>' + #13#10 +
    '  <log mode="roll-by-size"><sizeThreshold>10240</sizeThreshold><keepFiles>8</keepFiles></log>' + #13#10 +
    '</service>' + #13#10;
  if not SaveStringToFile(ExpandConstant('{app}\e-approval-kr-service.xml'), Xml, False) then
    RaiseException('cannot write service configuration');
end;

procedure LockDownDataDir;
var
  Data: String;
begin
  Data := ExpandConstant('{commonappdata}\{#AppName}');
  { SIDs, not names, so it works on every Windows display language:
    S-1-5-18 = SYSTEM, S-1-5-32-544 = Administrators, S-1-5-19 = LOCAL SERVICE }
  RunCmd(ExpandConstant('{sys}\icacls.exe'), '"' + Data + '" /inheritance:r /grant:r *S-1-5-18:(OI)(CI)F ' +
      '*S-1-5-32-544:(OI)(CI)F *S-1-5-19:(OI)(CI)M /T /C /Q');
end;

procedure FirewallRule(Add: Boolean);
var
  Netsh: String;
begin
  Netsh := ExpandConstant('{sys}\netsh.exe');
  RunCmd(Netsh, 'advfirewall firewall delete rule name="{#AppName}"');
  if Add then
    RunCmd(Netsh, 'advfirewall firewall add rule name="{#AppName}" dir=in action=allow protocol=TCP ' +
        'localport={#Port} remoteip=localsubnet profile=private,domain program="' +
        ExpandConstant('{app}\e-approval-kr.exe') + '"');
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
begin
  StopService;  { upgrade: release the files before they are replaced }
  Result := '';
end;

procedure CurStepChanged(CurStep: TSetupStep);
var
  Svc: String;
begin
  if CurStep <> ssPostInstall then
    Exit;
  Svc := ExpandConstant('{app}\e-approval-kr-service.exe');
  WriteServiceXml;
  LockDownDataDir;
  if not ServiceExists then
    if RunCmd(Svc, 'install') <> 0 then
      RaiseException('service registration failed (see setup log)');
  { least privilege: built-in LOCAL SERVICE account instead of LocalSystem }
  Sc('config {#SvcName} obj= "NT AUTHORITY\LocalService" password= ""');
  FirewallRule(WizardIsTaskSelected('firewall'));
  if RunCmd(ExpandConstant('{sys}\net.exe'), 'start {#SvcName}') <> 0 then
  begin
    if not WizardSilent then
      MsgBox(FmtMessage(CustomMessage('SvcFail'), [ExpandConstant('{commonappdata}\{#AppName}\logs')]), mbError, MB_OK)
    else
      Log('service failed to start');
  end
  else
    Sleep(1500);  { give the server a moment before the browser opens /setup }
end;

function HasParam(const P: String): Boolean;
var
  I: Integer;
begin
  Result := False;
  for I := 1 to ParamCount do
    if CompareText(ParamStr(I), P) = 0 then
      Result := True;
end;

function InitializeUninstall: Boolean;
var
  Data: String;
begin
  Data := ExpandConstant('{commonappdata}\{#AppName}');
  if UninstallSilent then
    PurgeData := HasParam('/PURGEDATA')
  else
    PurgeData := MsgBox(FmtMessage(CustomMessage('PurgeAsk'), [Data]), mbConfirmation,
                        MB_YESNO or MB_DEFBUTTON2) = IDYES;
  Result := True;
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  Svc: String;
begin
  if CurUninstallStep = usUninstall then
  begin
    StopService;
    Svc := ExpandConstant('{app}\e-approval-kr-service.exe');
    if ServiceExists then
      if RunCmd(Svc, 'uninstall') <> 0 then
        Sc('delete {#SvcName}');
    FirewallRule(False);
  end;
  if (CurUninstallStep = usPostUninstall) and PurgeData then
    DelTree(ExpandConstant('{commonappdata}\{#AppName}'), True, True, True);
end;
