{ Product directory policy. No shell, recursive mkdir or broad grants. }
type
  TTargetKind = (tkUnknown, tkNew, tkRepair, tkUpgrade);
var
  ValidatedTargetKind: TTargetKind;

function ReadAttributes(Name: String): LongWord;
  external 'GetFileAttributesW@kernel32.dll stdcall';
function TempFile(const Directory, Prefix: String; Unique: LongWord; Buffer: String): LongWord;
  external 'GetTempFileNameW@kernel32.dll stdcall';
function LastFileError: LongWord;
  external 'GetLastError@kernel32.dll stdcall';

function HumanSize(Size: Int64): String;
begin
  if Size >= 1073741824 then Result := Format('%.1f GB', [Size / 1073741824.0])
  else Result := Format('%.0f MB', [Size / 1048576.0]);
end;

function SafeAncestors(Path: String): Boolean;
var Parent: String; Attributes: LongWord;
begin
  Result := False;
  while Length(Path) > 3 do begin
    Attributes := ReadAttributes(Path);
    if (Attributes = $FFFFFFFF) or ((Attributes and $400) <> 0) then Exit;
    Parent := ExtractFileDir(Path);
    if CompareText(Parent, Path) = 0 then Exit;
    Path := Parent;
  end;
  Result := True;
end;

function KnownTree(Root, Relative, Inventory: String; Known: Boolean): Boolean;
var Find: TFindRec; Child, Full: String;
begin
  Result := True;
  if not FindFirst(AddBackslash(Root) + Relative + '*', Find) then begin
    Result := LastFileError = 2; { Empty only, not inaccessible. }
    Exit;
  end;
  try
    repeat
      if (Find.Name <> '.') and (Find.Name <> '..') then begin
        Child := Relative + Find.Name;
        Full := AddBackslash(Root) + Child;
        if (Find.Attributes and $400) <> 0 then begin Result := False; Exit; end;
        if not Known then begin Result := False; Exit; end;
        if (Find.Attributes and FILE_ATTRIBUTE_DIRECTORY) <> 0 then begin
          if GetIniString('Directories', Child, '', Inventory) <> '1' then begin Result := False; Exit; end;
          if not KnownTree(Root, Child + '\', Inventory, Known) then begin Result := False; Exit; end;
        end else if (CompareText(Child, '.mentorflow-install.ini') <> 0) and
          (CompareText(Child, 'unins000.exe') <> 0) and (CompareText(Child, 'unins000.dat') <> 0) then
          if GetIniString('Files', Child, '', Inventory) <> '1' then begin Result := False; Exit; end;
      end;
    until not FindNext(Find);
    Result := LastFileError = 18; { ERROR_NO_MORE_FILES; all other errors fail closed. }
  finally FindClose(Find); end;
end;

function ExistingKnownParent: String;
begin
  { Resolve on the user's machine. Never restore a development /PARENT value. }
  Result := ExpandConstant('{localappdata}');
  if DirExists(Result) then Exit;
  Result := ExpandConstant('{userappdata}');
  if DirExists(Result) then Exit;
  Result := ExpandConstant('{userdocs}');
  if DirExists(Result) then Exit;
  Result := ''; { Fail closed; the location page requests a real folder. }
end;

function ClassifyTarget(Target: String): TTargetKind;
begin
  Result := tkUnknown;
  if FileExists(Target) then Exit;
  if not DirExists(Target) then begin Result := tkNew; Exit; end;
  if not SafeAncestors(Target) then Exit;
  { UI hints only, never install authority. InstallThroughCore always performs
    signed core preflight/admission before creating or changing the App tree. }
  if FileExists(AddBackslash(Target) + 'per-user-install.json') and
     FileExists(AddBackslash(Target) + 'current-release.json') then begin
    Result := tkUpgrade;
    try
      if CompareText(GetSHA256OfFile(AddBackslash(Target) + 'App\MentorFlow.exe'),
                     '{#PayloadExeSHA256}') = 0 then Result := tkRepair;
    except Result := tkUnknown; end;
    Exit;
  end;
  if FileExists(AddBackslash(Target) + 'highest-release.json') and
     FileExists(AddBackslash(Target) + 'per-user-transaction.json') then begin
    Result := tkRepair; Exit; { Core proves complete signed repair evidence. }
  end;
  if KnownTree(Target, '', '', False) then Result := tkNew;
end;

function Writable(Directory: String): Boolean;
var Probe: String;
begin
  { GetTempFileName creates one uniquely named empty file; delete it immediately. }
  Probe := StringOfChar(#0, 32768);
  Result := TempFile(Directory, 'mf', 0, Probe) <> 0;
  if not Result then Exit;
  SetLength(Probe, Pos(#0, Probe) - 1);
  Result := (Probe <> '') and FileExists(Probe);
  if Result then Result := DeleteFile(Probe);
end;

function ValidateParent(Input: String; var Target: String; var NeedsAdmin: Boolean;
  CheckSpace: Boolean): String;
var Parent: String; Free, Total, Required: Int64; I: Integer;
begin
  Result := ''; Target := ''; NeedsAdmin := False; ValidatedTargetKind := tkUnknown;
  Parent := Trim(Input);
  if Length(Parent) < 3 then begin
    Result := '请选择本机已存在的完整文件夹路径。'; Exit;
  end;
  if (Parent[2] <> ':') or (Parent[3] <> '\') then begin
    Result := '请选择本机已存在的完整文件夹路径。'; Exit;
  end;
  if (Pos('"', Parent) > 0) or (Pos('*', Parent) > 0) or (Pos('?', Parent) > 0) or
     (Pos('/', Parent) > 0) or (Pos('\..', Parent) > 0) or (Pos('\.', Parent) > 0) or
     (Pos('.\', Parent) > 0) or (Pos(' \', Parent) > 0) or (Parent[Length(Parent)] = '.') then begin
    Result := '文件夹路径无效，请使用“浏览”选择已有文件夹。'; Exit;
  end;
  for I := 4 to Length(Parent) do
    if Parent[I] = ':' then begin Result := '文件夹路径无效。'; Exit; end;
  if not DirExists(Copy(Parent, 1, 3)) then begin Result := '此磁盘不存在或未连接，请选择可用磁盘。'; Exit; end;
  if not DirExists(Parent) then begin Result := '这个位置不存在，请选择一个已有文件夹。'; Exit; end;
  Parent := RemoveBackslashUnlessRoot(ExpandFileName(Parent));
  Target := AddBackslash(Parent) + 'MentorFlow';
  if not SafeAncestors(Parent) then begin Result := '请选择直接位于磁盘上的文件夹。'; Exit; end;
  ValidatedTargetKind := ClassifyTarget(Target);
  if ValidatedTargetKind = tkUnknown then begin
    Result := '这个文件夹里已经有其他文件，请换一个位置。'; Exit;
  end;
  Required := {#RequiredBytes};
  if CheckSpace then begin
    if not GetSpaceOnDisk64(Parent, Free, Total) then begin
      Result := '无法读取此磁盘的可用空间，请选择其他位置。'; Exit;
    end;
    if Free < Required then begin
      Result := '空间不足：MentorFlow 需要约 ' + HumanSize(Required) + '，当前这个磁盘只有 ' + HumanSize(Free) + ' 可用。'; Exit;
    end;
  end;
  if not Writable(Parent) then begin
    NeedsAdmin := not IsAdminInstallMode;
    if NeedsAdmin then Result := '此位置需要管理员权限'
    else Result := '此位置仍不可写，请返回选择其他文件夹。';
  end else if DirExists(Target) and not Writable(Target) then begin
    NeedsAdmin := not IsAdminInstallMode;
    if NeedsAdmin then Result := '此位置需要管理员权限'
    else Result := 'MentorFlow 文件夹不可写，请选择其他位置。';
  end;
end;
