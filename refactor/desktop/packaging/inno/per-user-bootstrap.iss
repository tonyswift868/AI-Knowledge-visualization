{ Adapter only: the pinned core owns feed verification, objects and the install transaction. }
function SetBootstrapTimer(hWnd: HWND; nIDEvent: UINT_PTR; uElapse: UINT; lpTimerFunc: NativeInt): UINT_PTR;
  external 'SetTimer@user32.dll stdcall';
function KillBootstrapTimer(hWnd: HWND; nIDEvent: UINT_PTR): BOOL;
  external 'KillTimer@user32.dll stdcall';

function BootstrapDownloadProgress(const Url, FileName: String; const Progress, ProgressMax: Int64): Boolean;
begin
  Result := not CoreDownloadPage.AbortedByUser;
end;

procedure BootstrapCoreProgress(Arg1: HWND; Arg2: UINT; Arg3: UINT_PTR; Arg4: DWORD);
var Percent: Integer;
begin
  if not CoreRunning then Exit;
  Percent := GetIniInt('install', 'percent', 0, 0, 100, CoreStatus);
  if Percent < 0 then Percent := 0;
  if Percent > 100 then Percent := 100;
  UpdateBrandedProgress(Percent, 100);
  ProgressCaption.Caption := GetIniString('install', 'stage', '正在校验安装文件…', CoreStatus);
  if CoreCancelRequested then ProgressCaption.Caption := '正在安全取消，请稍候…';
end;

function InstallThroughBootstrapCore(Target: String): String;
var Archive, ExtractRoot, Runtime, Parameters, Problem, Version: String;
  Code: Integer; Timer: UINT_PTR; CancelWasEnabled: Boolean;
begin
  Result := '';
  CoreCancelRequested := False;
  CoreDownloadPage.Clear;
  CoreDownloadPage.Add('{#BootstrapCoreURL}', 'bootstrap-core.zip', '{#BootstrapCoreSHA256}');
  CoreDownloadPage.Show;
  try
    try
      CoreDownloadPage.Download;
    except
      if CoreDownloadPage.AbortedByUser then Result := '安装已取消。您可以返回后重试。'
      else Result := '无法获取可信安装组件。请检查网络后重试。';
      Exit;
    end;
  finally
    CoreDownloadPage.Hide;
  end;
  if CoreDownloadPage.AbortedByUser then begin Result := '安装已取消。您可以返回后重试。'; Exit; end;

  Archive := ExpandConstant('{tmp}\bootstrap-core.zip');
  ExtractRoot := ExpandConstant('{tmp}\bootstrap-core');
  if DirExists(ExtractRoot) then DelTree(ExtractRoot, True, True, True);
  if not CreateDir(ExtractRoot) then begin Result := '无法准备安装组件临时目录。'; Exit; end;
  try
    ExtractArchive(Archive, ExtractRoot, '', True, nil);
  except
    Result := '安装组件解包失败，请重新下载后重试。'; Exit;
  end;
  Runtime := AddBackslash(ExtractRoot) + 'UserRuntime\UserRuntime.exe';
  if not FileExists(Runtime) then begin Result := '安装组件不完整。'; Exit; end;

  CoreStatus := ExpandConstant('{tmp}\bootstrap-status.ini');
  CoreCancelFile := ExpandConstant('{tmp}\bootstrap-cancel.signal');
  CoreResultFile := ExpandConstant('{tmp}\bootstrap-result.json');
  DeleteFile(CoreStatus); DeleteFile(CoreCancelFile); DeleteFile(CoreResultFile);
  Parameters := 'bootstrap-install --root ' + AddQuotes(Target) +
    ' --status-file ' + AddQuotes(CoreStatus) + ' --cancel-file ' + AddQuotes(CoreCancelFile) +
    ' --result ' + AddQuotes(CoreResultFile) + ' --close-after-health';
  CancelWasEnabled := WizardForm.CancelButton.Enabled;
  CoreRunning := True;
  Timer := 0;
  try
    WizardForm.CancelButton.Enabled := True;
    ShowBrandedPage(wpInstalling);
    UpdateBrandedProgress(0, 100);
    Timer := SetBootstrapTimer(0, 0, 150, CreateCallback(@BootstrapCoreProgress));
    if not Exec(Runtime, Parameters, ExtractRoot, SW_HIDE, ewWaitUntilTerminated, Code) or (Code <> 0) then begin
      if CoreCancelRequested then Result := '安装已取消。您可以返回重试或关闭安装器。'
      else begin
        Problem := GetIniString('install', 'error', '', CoreStatus);
        if Problem <> '' then Result := '安装未完成：' + Problem
        else Result := '安装未完成，请稍后重试。学习资料不会被删除。';
      end;
      Exit;
    end;
    Version := GetIniString('install', 'version', '', CoreStatus);
    if (Version = '') or (GetIniInt('install', 'percent', 0, 0, 100, CoreStatus) <> 100) then begin
      Result := '核心没有报告健康检查完成及实际应用版本。';
      Exit;
    end;
    VerifiedAppVersion := Version;
  finally
    if Timer <> 0 then KillBootstrapTimer(0, Timer);
    CoreRunning := False;
    WizardForm.CancelButton.Enabled := CancelWasEnabled;
  end;
end;
