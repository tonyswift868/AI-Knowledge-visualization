{ Finish-page tasks are chosen AFTER Inno's installation sections have run.
  The desktop action copies the real [Icons] Start Menu link; it never invents
  another launcher or pretends late task selection replays [Icons]. No action
  occurs until the user explicitly finishes with the corresponding task checked. }
const
  StartupRunKey = 'Software\Microsoft\Windows\CurrentVersion\Run';
var
  PreviousDesktopPath, PreviousDesktopHash, PreviousStartupCommand: String;

function FinishInventory: String;
begin
  Result := ExpandConstant('{app}\Maintenance\finish-options.ini');
end;

function NewInstallationShortcuts: Boolean;
begin
  Result := ValidatedTargetKind = tkNew;
end;

function CanonicalShortcutNeeded: Boolean;
begin
  Result := NewInstallationShortcuts or
    (((ValidatedTargetKind = tkRepair) or (ValidatedTargetKind = tkUpgrade)) and
     not FileExists(ExpandConstant('{autoprograms}\{#ShortcutName}.lnk')));
end;

function DesktopShortcutPath: String;
begin
  Result := ExpandConstant('{userdesktop}\{#ShortcutName}.lnk');
end;

function StartupCommand: String;
begin
  Result := AddQuotes(ExpandConstant('{app}\Updater\UserRuntime.exe')) +
    ' launch --root ' + AddQuotes(ExpandConstant('{app}'));
end;

function CanLaunchMentorFlow: Boolean;
begin
  { runasoriginaluser cannot undo a caller that was already elevated. Do not
    fall back to that token. Normal per-user setup is non-administrative. }
  Result := not IsAdmin;
end;

function FinishValue(Name, Value: String): Boolean;
begin
  Result := SetIniString('FinishOptions', Name, Value, FinishInventory);
end;

function OwnsDesktopShortcut: Boolean;
var Expected: String;
begin
  Result := False;
  if CompareText(GetIniString('FinishOptions', 'DesktopPath', '', FinishInventory),
                 DesktopShortcutPath) <> 0 then Exit;
  Expected := GetIniString('FinishOptions', 'DesktopSHA256', '', FinishInventory);
  if (Length(Expected) <> 64) or not FileExists(DesktopShortcutPath) then Exit;
  try
    Result := CompareText(Expected, GetSHA256OfFile(DesktopShortcutPath)) = 0;
  except Result := False; end;
end;

procedure CapturePreviousFinishOptions(Target: String);
var Inventory: String;
begin
  PreviousDesktopPath := ''; PreviousDesktopHash := ''; PreviousStartupCommand := '';
  if (ValidatedTargetKind <> tkRepair) and (ValidatedTargetKind <> tkUpgrade) then Exit;
  Inventory := AddBackslash(Target) + 'Maintenance\finish-options.ini';
  PreviousDesktopPath := GetIniString('FinishOptions', 'DesktopPath', '', Inventory);
  PreviousDesktopHash := GetIniString('FinishOptions', 'DesktopSHA256', '', Inventory);
  PreviousStartupCommand := GetIniString('FinishOptions', 'StartupCommand', '', Inventory);
end;

procedure RestorePreviousFinishOptions;
begin
  { Keep prior opt-in ownership across repair. This neither enables startup nor
    creates a shortcut; unchecked choices never remove a user's existing choice. }
  if (CompareText(PreviousDesktopPath, DesktopShortcutPath) = 0) and
     (Length(PreviousDesktopHash) = 64) then begin
    if not FinishValue('DesktopPath', PreviousDesktopPath) or
       not FinishValue('DesktopSHA256', PreviousDesktopHash) then
      RaiseException('无法保留已有快捷方式设置。');
  end;
  if PreviousStartupCommand = StartupCommand then
    if not FinishValue('StartupCommand', PreviousStartupCommand) then
      RaiseException('无法保留已有开机启动设置。');
end;

function ApplyFinishOptions: String;
var SourceLink, LinkHash, ExistingCommand: String;
begin
  Result := '';
  { Explicit finish choices also apply on upgrade/repair. Unchecked choices
    leave existing shortcuts/startup preferences untouched. }
  if not WizardIsTaskSelected('desktopicon') and not WizardIsTaskSelected('startup') then Exit;
  if IsAdminInstallMode or IsAdmin then begin
    Result := '可选设置需要普通用户权限，请取消勾选后继续。'; Exit;
  end;
  if WizardIsTaskSelected('desktopicon') then begin
    if FileExists(DesktopShortcutPath) then begin
      if not OwnsDesktopShortcut then begin
        Result := '桌面已有同名快捷方式，未覆盖。请取消勾选后继续。'; Exit;
      end;
    end else begin
      { [Icons] owns the canonical target/arguments. Copy only on explicit opt-in. }
      SourceLink := ExpandConstant('{autoprograms}\{#ShortcutName}.lnk');
      if not FileExists(SourceLink) then begin
        Result := '无法创建桌面快捷方式，请取消勾选后继续。'; Exit;
      end;
      try LinkHash := GetSHA256OfFile(SourceLink);
      except Result := '无法读取应用快捷方式，请取消勾选后继续。'; Exit; end;
      if not FinishValue('DesktopPath', DesktopShortcutPath) or
         not FinishValue('DesktopSHA256', LinkHash) then begin
        Result := '无法保存快捷方式设置，请取消勾选后继续。'; Exit;
      end;
      if not CopyFile(SourceLink, DesktopShortcutPath, True) then begin
        Result := '无法创建桌面快捷方式，已有文件未覆盖。请取消勾选后继续。'; Exit;
      end;
    end;
  end;
  if WizardIsTaskSelected('startup') then begin
    { Exact per-user Run value only; never overwrite another install's command. }
    if RegQueryStringValue(HKCU, StartupRunKey, '{#ShortcutName}', ExistingCommand) then
      if ExistingCommand <> StartupCommand then begin
        Result := '已有不同的开机启动设置，未修改。请取消勾选后继续。'; Exit;
      end;
    if not FinishValue('StartupCommand', StartupCommand) then begin
      Result := '无法保存开机启动设置，请取消勾选后继续。'; Exit;
    end;
    if not RegWriteStringValue(HKCU, StartupRunKey, '{#ShortcutName}', StartupCommand) then
      Result := '无法设置开机启动，请取消勾选后继续。';
  end;
end;

procedure RemoveOwnedFinishOptions;
var ExistingCommand, RecordedCommand: String;
begin
  { Before Inno removes the inventory. Preserve changed/foreign shortcuts and
    values; do not remove a Run key or another installation's startup command. }
  if OwnsDesktopShortcut then DeleteFile(DesktopShortcutPath);
  RecordedCommand := GetIniString('FinishOptions', 'StartupCommand', '', FinishInventory);
  if RecordedCommand <> StartupCommand then Exit;
  if RegQueryStringValue(HKCU, StartupRunKey, '{#ShortcutName}', ExistingCommand) then
    if ExistingCommand = RecordedCommand then RegDeleteValue(HKCU, StartupRunKey, '{#ShortcutName}');
end;
