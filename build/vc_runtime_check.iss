// Shared by the real installer and the executable Windows regression harness.
function VCRuntimeNeedsUpgrade(Installed: Cardinal; Version, Required: String): Boolean;
var
  InstalledVersion, RequiredVersion: Int64;
begin
  Result := True;
  if Installed <> 1 then exit;
  if (Length(Version) > 0) and (Version[1] = 'v') then
    Delete(Version, 1, 1);
  if not StrToVersion(Version, InstalledVersion) then exit;
  if not StrToVersion(Required, RequiredVersion) then exit;
  Result := ComparePackedVersion(InstalledVersion, RequiredVersion) < 0;
end;
