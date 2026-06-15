; installer.iss — Inno Setup 6 script for WindowControl

#define MyAppName "WindowControl"
#define MyAppVersion "1.2.12"
#define MyAppPublisher "WindowControl"
#define MyAppExeName "WindowControl.exe"

[Setup]
AppId={{A1B2C3D4-E5F6-7890-ABCD-EF1234567890}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
AllowNoIcons=yes
OutputDir=..\release
OutputBaseFilename=WindowControlInstaller
SetupIconFile=..\src\assets\icon.ico
Compression=lzma
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=admin

; Tailscale recommendation (not mandatory)
[Code]
function NeedsVCRedist(): Boolean;
var
  Installed: Cardinal;
begin
  // Check for VC++ 2015-2022 x64 (minimum version 14.0)
  Result := not RegQueryDWordValue(
    HKLM,
    'SOFTWARE\Microsoft\VisualStudio\14.0\VC\Runtimes\x64',
    'Installed',
    Installed
  ) or (Installed = 0);
end;

procedure StopAndRemoveService();
var
  ResultCode: Integer;
begin
  Exec('sc.exe', 'stop WindowControlService', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
  Sleep(2000);
  Exec('sc.exe', 'delete WindowControlService', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
  Sleep(1000);
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep = ssInstall then
    StopAndRemoveService();
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  if CurUninstallStep = usUninstall then
    StopAndRemoveService();
end;

function InitializeSetup(): Boolean;
var
  HasTailscale: Boolean;
  Answer: Integer;
begin
  HasTailscale := FileExists('C:\Program Files\Tailscale\tailscale.exe');
  if not HasTailscale then begin
    Answer := MsgBox(
      'Tailscale is not installed. WindowControl works best with Tailscale for remote access.' + #13#10 +
      'You can still use it on your local network.' + #13#10#13#10 +
      'Continue installation without Tailscale?',
      mbConfirmation, MB_YESNO
    );
    Result := (Answer = IDYES);
  end else begin
    Result := True;
  end;
end;

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked
Name: "startupicon"; Description: "Start WindowControl with Windows"; GroupDescription: "Startup:"; Flags: unchecked

[Files]
; One-dir build: copy entire dist\WindowControl\ folder contents
Source: "..\dist\WindowControl\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
; VC++ 2015-2022 x64 redistributable (downloaded by CI, bundled here)
Source: "..\build\vc_redist.x64.exe"; DestDir: "{tmp}"; Flags: deleteafterinstall; Check: NeedsVCRedist

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{group}\Uninstall {#MyAppName}"; Filename: "{uninstallexe}"
Name: "{commondesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Registry]
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: string; ValueName: "{#MyAppName}"; ValueData: """{app}\{#MyAppExeName}"""; Flags: uninsdeletevalue; Tasks: startupicon

[Run]
Filename: "{tmp}\vc_redist.x64.exe"; Parameters: "/quiet /norestart"; StatusMsg: "Installing Visual C++ Runtime..."; Flags: waituntilterminated; Check: NeedsVCRedist
Filename: "{app}\{#MyAppExeName}"; Parameters: "--install"; StatusMsg: "Installing lock screen service..."; Flags: waituntilterminated runhidden
Filename: "{app}\{#MyAppExeName}"; Description: "{cm:LaunchProgram,{#StringChange(MyAppName, '&', '&&')}}"; Flags: nowait postinstall skipifsilent
