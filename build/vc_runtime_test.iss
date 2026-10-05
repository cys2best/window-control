; Executes the exact comparison used by installer.iss without modifying HKLM.
[Setup]
AppName=VC Runtime Regression
AppVersion=1
CreateAppDir=no
Uninstallable=no
PrivilegesRequired=lowest
OutputDir=..\release
OutputBaseFilename=VCRuntimeRegression

[Code]
#include "vc_runtime_check.iss"

procedure ExpectUpgrade(Installed: Cardinal; Version: String; Expected: Boolean);
begin
  if VCRuntimeNeedsUpgrade(Installed, Version, '14.51.36231.0') <> Expected then
    RaiseException('Unexpected runtime upgrade decision for ' + Version);
end;

function InitializeSetup(): Boolean;
begin
  ExpectUpgrade(0, 'v14.51.36247.0', True);
  ExpectUpgrade(1, 'v14.44.35211.0', True);
  ExpectUpgrade(1, 'v14.51.36230.99', True);
  ExpectUpgrade(1, 'v14.51.36231.0', False);
  ExpectUpgrade(1, 'v14.51.36247.0', False);
  ExpectUpgrade(1, 'v14.52.1.0', False);
  ExpectUpgrade(1, '', True);
  ExpectUpgrade(1, 'unknown', True);
  if not SaveStringToFile(ExpandConstant('{param:RESULT}'), '8 runtime regression cases passed', False) then
    RaiseException('Could not save regression result');
  Result := False; // No installation; caller requires the result file.
end;
