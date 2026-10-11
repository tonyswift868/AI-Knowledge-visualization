{ Adapter only: no release parser, copy/swap implementation or elevation here. }
function SetCoreTimer(hWnd: HWND; nIDEvent: UINT_PTR; uElapse: UINT; lpTimerFunc: NativeInt): UINT_PTR;
  external 'SetTimer@user32.dll stdcall';
function KillCoreTimer(hWnd: HWND; nIDEvent: UINT_PTR): BOOL;
  external 'KillTimer@user32.dll stdcall';

procedure CoreProgress(Arg1: HWND; Arg2: UINT; Arg3: UINT_PTR; Arg4: DWORD);
var Percent: Integer;
begin
  if not CoreRunning then Exit;
  Percent := GetIniInt('install', 'percent', 0, 0, 100, CoreStatus);
  if Percent < 0 then Percent := 0;
  if Percent > 100 then Percent := 100;
  { The timer only reads real core byte-copy/verify/health progress. }
  UpdateBrandedProgress(Percent, 100);
  ProgressCaption.Caption := GetIniString('install', 'stage', '正在校验安装文件…', CoreStatus);
  if CoreCancelRequested then ProgressCaption.Caption := '正在安全取消，请稍候…';
end;

function InstallThroughCore(Target: String): String;
var Runtime, Parameters, Problem: String; Code: Integer; Timer: UINT_PTR;
  CancelWasEnabled: Boolean;
begin
  Result := '';
  Inc(CoreAttempt);
  CoreStatus := ExpandConstant('{tmp}\core-status-') + IntToStr(CoreAttempt) + '.ini';
  CoreCancelFile := ExpandConstant('{tmp}\core-cancel-') + IntToStr(CoreAttempt) + '.signal';
  CoreCancelRequested := False;
  { Extract only to Inno's private scratch; never directly into the target. }
  ExtractTemporaryFiles('{tmp}\payload\*');
  ExtractTemporaryFile('base-release.json');
  Runtime := ExpandConstant('{tmp}\payload\UserRuntime\UserRuntime.exe');
  if not Exec(Runtime, 'preflight --root ' + AddQuotes(Target), '', SW_HIDE,
      ewWaitUntilTerminated, Code) or (Code <> 0) then begin
    Result := '安装位置未通过核心校验。请选择已有、可写且无未知文件的位置。';
    Exit;
  end;
  Parameters := 'install --root ' + AddQuotes(Target) +
    ' --source ' + AddQuotes(ExpandConstant('{tmp}\payload')) +
    ' --release ' + AddQuotes(ExpandConstant('{tmp}\base-release.json')) +
    ' --status-file ' + AddQuotes(CoreStatus) + ' --cancel-file ' + AddQuotes(CoreCancelFile) +
    ' --result ' + AddQuotes(ExpandConstant('{tmp}\core-result.json')) + ' --close-after-health';
  CancelWasEnabled := WizardForm.CancelButton.Enabled;
  CoreRunning := True;
  Timer := 0;
  try
    { Inno disables Cancel before PrepareToInstall. While the core owns work,
      X and the progress button must reach our cancellation-signal handler. }
    WizardForm.CancelButton.Enabled := True;
    ShowBrandedPage(wpInstalling);
    UpdateBrandedProgress(0, 100);
    Timer := SetCoreTimer(0, 0, 150, CreateCallback(@CoreProgress));
    if not Exec(Runtime, Parameters, '', SW_HIDE, ewWaitUntilTerminated, Code) or (Code <> 0) then begin
      { Raw core diagnostics remain in core-result.json, not ordinary UI. }
      if CoreCancelRequested then Result := '安装已取消。您可以返回重试或关闭安装器。'
      else begin
        Problem := GetIniString('install', 'error', '', CoreStatus);
        if Pos('close the current App before continuing', Problem) > 0 then
          Result := '请先关闭 MentorFlow，再点击“上一步”后继续安装。学习资料不会被删除。'
        else if Pos('请先关闭 MentorFlow', Problem) > 0 then Result := Problem
        else Result := '安装未完成，请稍后重试。学习资料不会被删除。';
      end;
    end;
  finally
    if Timer <> 0 then KillCoreTimer(0, Timer);
    CoreRunning := False;
    WizardForm.CancelButton.Enabled := CancelWasEnabled;
  end;
end;
