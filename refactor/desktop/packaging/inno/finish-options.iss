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

function DesktopShortcutMatches(SourceLink: String): Boolean;
var Shell, DesktopLink, Source: Variant;
begin
  Result := False;
  if not FileExists(DesktopShortcutPath) or not FileExists(SourceLink) then Exit;
  { Compare the link's launch contract, not its icon or other user-editable data. }
  try
    Shell := CreateOleObject('WScript.Shell');
    DesktopLink := Shell.CreateShortcut(DesktopShortcutPath);
    Source := Shell.CreateShortcut(SourceLink);
    Result := (CompareText(DesktopLink.TargetPath, Source.TargetPath) = 0) and
      (CompareText(DesktopLink.Arguments, Source.Arguments) = 0);
  except
    Log('Finish options: unable to inspect desktop shortcut.');
  end;
end;

procedure AppendFinishMessage(var Messages: String; Message: String);
begin
  if Messages = '' then Messages := Message
  else Messages := Messages + '；' + Message;
  Log('Finish options: ' + Message);
end;

procedure DeselectFinishTask(Name: String);
begin
  { The two optional tasks are declared in this order in both product scripts;
    update the visible check boxes directly because WizardSelectTasks only
    applies reliably before the task page is constructed. }
  if (Name = 'desktopicon') and (WizardForm.TasksList.Items.Count > 0) then
    WizardForm.TasksList.Checked[0] := False
  else if (Name = 'startup') and (WizardForm.TasksList.Items.Count > 1) then
    WizardForm.TasksList.Checked[1] := False;
end;

function PreviewFinishOptions: String;
var SourceLink, ExistingCommand: String;
begin
  Result := '';
  if not WizardIsTaskSelected('desktopicon') and not WizardIsTaskSelected('startup') then Exit;
  if IsAdminInstallMode or IsAdmin then begin
    DeselectFinishTask('desktopicon');
    DeselectFinishTask('startup');
    AppendFinishMessage(Result, '可选设置需要普通用户权限，已保留原样');
    Exit;
  end;
  if WizardIsTaskSelected('desktopicon') then begin
    SourceLink := ExpandConstant('{autoprograms}\{#ShortcutName}.lnk');
    if FileExists(DesktopShortcutPath) then begin
      if not OwnsDesktopShortcut and not DesktopShortcutMatches(SourceLink) then begin
        DeselectFinishTask('desktopicon');
        AppendFinishMessage(Result, '桌面已有同名快捷方式，保留原样');
      end;
    end else if not FileExists(SourceLink) then begin
      DeselectFinishTask('desktopicon');
      AppendFinishMessage(Result, '无法找到开始菜单快捷方式，保留桌面原样');
    end;
  end;
  if WizardIsTaskSelected('startup') then begin
    if RegQueryStringValue(HKCU, StartupRunKey, '{#ShortcutName}', ExistingCommand) and
       (CompareText(ExistingCommand, StartupCommand) <> 0) then begin
      DeselectFinishTask('startup');
      AppendFinishMessage(Result, '已有不同的开机启动设置，保留原样');
    end;
  end;
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
    AppendFinishMessage(Result, '可选设置需要普通用户权限，已保留原样');
  end;
  if not (IsAdminInstallMode or IsAdmin) then begin
    if WizardIsTaskSelected('desktopicon') then begin
      if FileExists(DesktopShortcutPath) then begin
        if not OwnsDesktopShortcut and
           not DesktopShortcutMatches(ExpandConstant('{autoprograms}\{#ShortcutName}.lnk')) then
          AppendFinishMessage(Result, '桌面已有同名快捷方式，未覆盖');
      end else begin
        { [Icons] owns the canonical target/arguments. Copy only on explicit opt-in. }
        SourceLink := ExpandConstant('{autoprograms}\{#ShortcutName}.lnk');
        if not FileExists(SourceLink) then begin
          AppendFinishMessage(Result, '无法找到开始菜单快捷方式，未创建桌面快捷方式');
        end else begin
          try LinkHash := GetSHA256OfFile(SourceLink);
          except
            LinkHash := '';
            AppendFinishMessage(Result, '无法读取应用快捷方式，未创建桌面快捷方式');
          end;
          if LinkHash <> '' then begin
            if not CopyFile(SourceLink, DesktopShortcutPath, True) then
              AppendFinishMessage(Result, '无法创建桌面快捷方式，已有文件未覆盖')
            else if not FinishValue('DesktopPath', DesktopShortcutPath) or
               not FinishValue('DesktopSHA256', LinkHash) then begin
              DeleteFile(DesktopShortcutPath);
              AppendFinishMessage(Result, '无法保存快捷方式设置，未保留新快捷方式');
            end;
          end;
        end;
      end;
    end;
    if WizardIsTaskSelected('startup') then begin
      { Exact per-user Run value only; never overwrite another install's command. }
      if RegQueryStringValue(HKCU, StartupRunKey, '{#ShortcutName}', ExistingCommand) then begin
        if CompareText(ExistingCommand, StartupCommand) <> 0 then
          AppendFinishMessage(Result, '已有不同的开机启动设置，未修改')
        else
          Log('Finish options: existing startup command already matches.');
      end else begin
        if not RegWriteStringValue(HKCU, StartupRunKey, '{#ShortcutName}', StartupCommand) then
          AppendFinishMessage(Result, '无法设置开机启动，未修改')
        else if not FinishValue('StartupCommand', StartupCommand) then begin
          RegDeleteValue(HKCU, StartupRunKey, '{#ShortcutName}');
          AppendFinishMessage(Result, '无法保存开机启动设置，未保留新开机项');
        end;
      end;
    end;
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
