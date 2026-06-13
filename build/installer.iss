; installer.iss — Inno Setup 6 script for WindowControl

#define MyAppName "WindowControl"
#define MyAppVersion "1.0.0"
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
Source: "..\dist\{#MyAppExeName}"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{group}\Uninstall {#MyAppName}"; Filename: "{uninstallexe}"
Name: "{commondesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Registry]
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: string; ValueName: "{#MyAppName}"; ValueData: """{app}\{#MyAppExeName}"""; Flags: uninsdeletevalue; Tasks: startupicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "{cm:LaunchProgram,{#StringChange(MyAppName, '&', '&&')}}"; Flags: nowait postinstall skipifsilent
