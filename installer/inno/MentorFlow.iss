#if Ver < EncodeVer(7, 0, 0)
  #error Inno Setup 7 or newer is required.
#endif
#ifndef PayloadDir
  #error Use scripts/build_inno_installer.py with a verified payload.
#endif

[Setup]
AppId={#ProductAppId}
AppName=MentorFlow
AppVersion={#ProductVersion}
AppPublisher=MentorFlow
DefaultDirName={localappdata}\Programs\MentorFlow
DefaultGroupName=MentorFlow
SetupArchitecture=x64
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
PrivilegesRequired=lowest
UsePreviousPrivileges=no
UsePreviousAppDir=no
WizardStyle=modern {#Appearance} windows11 hidebevels includetitlebar
WizardBackColor=#fffdf8
WizardBackColorDynamicDark=#242127
SetupIconFile={#BuildInput}\MentorFlow.ico
WizardSmallImageFile={#BuildInput}\MentorFlow.png
WizardSmallImageFileDynamicDark={#BuildInput}\MentorFlow.png
WizardImageFile=
WizardImageFileDynamicDark=
DisableWelcomePage=yes
DisableDirPage=yes
DisableProgramGroupPage=yes
DisableReadyPage=yes
DisableStartupPrompt=yes
DirExistsWarning=no
AllowRootDirectory=no
AllowNetworkDrive=no
AllowUNCPath=no
UninstallDisplayIcon={app}\App\MentorFlow.exe
UninstallFilesDir={app}\Maintenance
OutputDir={#OutputDir}
OutputBaseFilename={#OutputName}
Compression=lzma2/fast
SolidCompression=no
DiskSpanning=no
CloseApplications=no
RestartApplications=no
SetupLogging=yes
SetupMutex=MentorFlow.Installer
ShowLanguageDialog=no
#if Int(FixtureBuild)
Uninstallable=no
CreateUninstallRegKey=no
#endif

[Languages]
Name: "chinesesimp"; MessagesFile: "compiler:Languages\ChineseSimplified.isl"

[LangOptions]
DialogFontName=Segoe UI
DialogFontSize=9

[Messages]
SetupWindowTitle=安装 MentorFlow
WizardInstalling=正在安装 MentorFlow
InstallingLabel=正在准备应用…
FinishedHeadingLabel=MentorFlow 已安装
FinishedLabel=现在可以开始学习。
FinishedLabelNoIcons=现在可以开始学习。
ButtonNext=安装(&I)
ButtonInstall=安装(&I)
ButtonCancel=取消
ButtonFinish=启动 MentorFlow

[Files]
#if Int(FixtureBuild)
Source: "{#PayloadDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "{#BuildInput}\ownership.ini"; DestDir: "{app}"; DestName: ".mentorflow-install.ini"; Flags: ignoreversion
#else
; Only the signed core owns App/Updater/receipts. Inno owns maintenance files.
Source: "{#PayloadDir}\*"; DestDir: "{tmp}\payload"; Flags: dontcopy recursesubdirs createallsubdirs
Source: "{#SignedRelease}"; DestDir: "{tmp}"; DestName: "base-release.json"; Flags: dontcopy
Source: "{#BuildInput}\shell.ini"; DestDir: "{app}\Maintenance"; Flags: ignoreversion
#endif
Source: "{#BuildInput}\MentorFlow.png"; Flags: dontcopy

#if !Int(FixtureBuild)
[Icons]
Name: "{autoprograms}\{#ShortcutName}"; Filename: "{app}\Updater\UserRuntime.exe"; Parameters: "launch --root ""{app}"""; WorkingDir: "{app}"; IconFilename: "{app}\App\MentorFlow.exe"
#endif

[Run]
#if Int(FixtureBuild)
Filename: "{app}\MentorFlow.exe"; Description: "启动 MentorFlow"; Flags: nowait postinstall skipifsilent runasoriginaluser unchecked
#else
Filename: "{app}\Updater\UserRuntime.exe"; Parameters: "launch --root ""{app}"""; Description: "启动 MentorFlow"; Flags: nowait postinstall skipifsilent runasoriginaluser unchecked
#endif

[Code]
var
  LocationPage: TWizardPage;
  SelectedParent: String;
  CoreRunning, CoreCancelRequested: Boolean;
  CoreStatus, CoreCancelFile: String;
  CoreAttempt: Integer;

#include "directory-policy.iss"

#include "branded-pages.iss"

#if !Int(FixtureBuild)
#include "per-user-core.iss"
#endif

procedure InitializeWizard;
begin
  LocationPage := CreateCustomPage(wpWelcome, '选择安装位置', '');
  SelectedParent := ExpandConstant('{param:PARENT|{localappdata}\Programs}');
  CreateBrandedPages;
  RefreshLocation(nil);
end;

procedure CurPageChanged(CurPageID: Integer);
begin
  ShowBrandedPage(CurPageID);
end;

function NextButtonClick(CurPageID: Integer): Boolean;
var Target, Problem: String; NeedsAdmin: Boolean;
begin
  Result := True;
  if CurPageID = LocationPage.ID then begin
    Problem := ValidateParent(SelectedParent, Target, NeedsAdmin, True);
    Result := Problem = '';
    if Result then WizardForm.DirEdit.Text := Target else RefreshLocation(nil);
  end;
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
var Target: String; NeedsAdmin: Boolean;
begin
  Result := ValidateParent(SelectedParent, Target, NeedsAdmin, True);
#if Int(FixtureBuild)
  if (Result = '') and (Pos('e:\codex\temp\', Lowercase(Target)) <> 1) then
    Result := '预览安装请选择 E:\Codex\Temp 中已有的文件夹。';
#endif
  if (Result = '') and (CompareText(Target, WizardDirValue) <> 0) then
    Result := '安装位置已改变，请返回重新选择。';
#if Int(FixtureBuild)
  { Legacy inert UX fixture only. Production directory creation belongs to core. }
  if (Result = '') and not DirExists(Target) then
    if not CreateDir(Target) then Result := '无法创建 MentorFlow 文件夹，请选择其他位置。';
#else
  if Result = '' then Result := InstallThroughCore(Target);
#endif
end;

procedure CurInstallProgressChanged(CurProgress, MaxProgress: Integer);
begin
  UpdateBrandedProgress(CurProgress, MaxProgress);
end;

procedure CancelButtonClick(CurPageID: Integer; var Cancel, Confirm: Boolean);
begin
  if CoreRunning then begin
    Cancel := False;
    Confirm := False;
    CoreCancelRequested := True;
    SaveStringToFile(CoreCancelFile, 'cancel', False);
    Exit;
  end;
  { Before copying any files, cancelling needs no extra modal. }
  if CurPageID = LocationPage.ID then Confirm := False;
end;

#if !Int(FixtureBuild)
procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var Code: Integer;
begin
  { usUninstall is after the user's confirmation, before Inno removes files. }
  if CurUninstallStep <> usUninstall then Exit;
  { On failure leave Inno registration and shortcuts intact for recovery. }
  if not Exec(ExpandConstant('{app}\Updater\UserRuntime.exe'),
    'uninstall --root ' + AddQuotes(ExpandConstant('{app}')), ExpandConstant('{app}'),
    SW_HIDE, ewWaitUntilTerminated, Code) or (Code <> 0) then
      RaiseException('卸载未完成。请先关闭 MentorFlow 后重试；资料不会被删除。');
end;
#endif

#if Int(FixtureBuild)
#include "probe.iss"
#endif
