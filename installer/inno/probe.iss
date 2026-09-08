{ Compiled ONLY in the inert UX fixture. Executes the same production policy. }
function InitializeSetup: Boolean;
var ProbeFile, Target, Problem, Parent: String; NeedsAdmin: Boolean;
begin
  Result := True;
  ProbeFile := ExpandConstant('{param:UXPROBE|}');
  if ProbeFile = '' then Exit;
  Parent := GetIniString('Probe', 'Parent', '', ProbeFile);
  Problem := ValidateParent(Parent, Target, NeedsAdmin, True);
  SetIniString('Result', 'Target', Target, ProbeFile);
  SetIniString('Result', 'Error', Problem, ProbeFile);
  SetIniString('Result', 'Accepted', IntToStr(Ord(Problem = '')), ProbeFile);
  SetIniString('Result', 'NeedsAdmin', IntToStr(Ord(NeedsAdmin)), ProbeFile);
  Result := False;
end;
